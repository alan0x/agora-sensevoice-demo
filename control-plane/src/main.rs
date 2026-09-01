use std::{
    collections::HashMap,
    env,
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    path::PathBuf,
    sync::{Arc, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};

use salvo::{
    http::{HeaderValue, StatusCode, header},
    prelude::*,
    serve_static::StaticDir,
    websocket::{Message, WebSocket, WebSocketUpgrade},
};
use serde::Serialize;
use serde_json::{Value, json};
use tokio::sync::{Mutex, mpsc};
use tracing::{error, info, warn};
use uuid::Uuid;

static APP_STATE: OnceLock<Arc<AppState>> = OnceLock::new();

#[derive(Clone, Debug)]
struct Config {
    bind_addr: String,
    static_dir: PathBuf,
    public_base_url: String,
    allowed_origin: Option<String>,
    agora_app_id: String,
    channel: String,
    client_uid: u32,
    bridge_uid: u32,
    client_rtc_token: String,
    bridge_rtc_token: String,
    bridge_shared_secret: String,
    session_ttl_seconds: u64,
    demo_mode: bool,
}

impl Config {
    fn from_env() -> Result<Self, String> {
        let demo_mode = env_bool("DEMO_MODE", false)?;
        let config = Self {
            bind_addr: env_or("BIND_ADDR", "0.0.0.0:8080"),
            static_dir: PathBuf::from(env_or("STATIC_DIR", "static")),
            public_base_url: env_or("PUBLIC_BASE_URL", "http://localhost:8080")
                .trim_end_matches('/')
                .to_owned(),
            allowed_origin: env::var("ALLOWED_ORIGIN")
                .ok()
                .filter(|value| !value.trim().is_empty()),
            agora_app_id: env::var("AGORA_APP_ID").unwrap_or_default(),
            channel: env_or("DEMO_CHANNEL", "sensevoice-demo"),
            client_uid: env_u32("DEMO_CLIENT_UID", 1001)?,
            bridge_uid: env_u32("DEMO_BRIDGE_UID", 9001)?,
            client_rtc_token: env::var("DEMO_CLIENT_RTC_TOKEN").unwrap_or_default(),
            bridge_rtc_token: env::var("DEMO_BRIDGE_RTC_TOKEN").unwrap_or_default(),
            bridge_shared_secret: env::var("BRIDGE_SHARED_SECRET").unwrap_or_default(),
            session_ttl_seconds: env_u64("SESSION_TTL_SECONDS", 900)?,
            demo_mode,
        };
        config.validate()?;
        Ok(config)
    }

    fn validate(&self) -> Result<(), String> {
        if self.bridge_shared_secret.len() < 16 {
            return Err("BRIDGE_SHARED_SECRET must contain at least 16 characters".into());
        }
        if !self.demo_mode {
            for (name, value) in [
                ("AGORA_APP_ID", &self.agora_app_id),
                ("DEMO_CLIENT_RTC_TOKEN", &self.client_rtc_token),
                ("DEMO_BRIDGE_RTC_TOKEN", &self.bridge_rtc_token),
            ] {
                if value.trim().is_empty() {
                    return Err(format!("{name} is required when DEMO_MODE=false"));
                }
            }
        }
        if self.client_uid == self.bridge_uid {
            return Err("DEMO_CLIENT_UID and DEMO_BRIDGE_UID must be different".into());
        }
        Ok(())
    }
}

fn env_or(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

fn env_bool(name: &str, default: bool) -> Result<bool, String> {
    match env::var(name) {
        Ok(value) => value
            .parse::<bool>()
            .map_err(|_| format!("{name} must be true or false")),
        Err(_) => Ok(default),
    }
}

fn env_u32(name: &str, default: u32) -> Result<u32, String> {
    match env::var(name) {
        Ok(value) => value
            .parse::<u32>()
            .map_err(|_| format!("{name} must be an unsigned integer")),
        Err(_) => Ok(default),
    }
}

fn env_u64(name: &str, default: u64) -> Result<u64, String> {
    match env::var(name) {
        Ok(value) => value
            .parse::<u64>()
            .map_err(|_| format!("{name} must be an unsigned integer")),
        Err(_) => Ok(default),
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct Session {
    id: String,
    ticket: String,
    channel: String,
    client_uid: u32,
    bridge_uid: u32,
    state: String,
    created_at_ms: u64,
    expires_at_ms: u64,
}

#[derive(Clone)]
struct SocketLink {
    id: Uuid,
    tx: mpsc::UnboundedSender<Message>,
}

#[derive(Default)]
struct Inner {
    sessions: HashMap<String, Session>,
    bridge: Option<SocketLink>,
    clients: HashMap<String, SocketLink>,
}

struct AppState {
    config: Config,
    inner: Mutex<Inner>,
}

impl AppState {
    fn new(config: Config) -> Self {
        Self {
            config,
            inner: Mutex::new(Inner::default()),
        }
    }
}

fn state() -> &'static Arc<AppState> {
    APP_STATE.get().expect("application state initialized")
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

fn run_healthcheck() -> Result<(), String> {
    let bind_addr = env_or("BIND_ADDR", "0.0.0.0:8080");
    let connect_addr = bind_addr
        .strip_prefix("0.0.0.0:")
        .map(|port| format!("127.0.0.1:{port}"))
        .unwrap_or(bind_addr);
    let socket_addr = connect_addr
        .to_socket_addrs()
        .map_err(|error| error.to_string())?
        .next()
        .ok_or_else(|| "healthcheck address did not resolve".to_owned())?;
    let mut stream = TcpStream::connect_timeout(&socket_addr, std::time::Duration::from_secs(2))
        .map_err(|error| error.to_string())?;
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(2)))
        .map_err(|error| error.to_string())?;
    stream
        .write_all(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .map_err(|error| error.to_string())?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| error.to_string())?;
    if response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200") {
        Ok(())
    } else {
        Err("health endpoint did not return HTTP 200".into())
    }
}

fn send_json(tx: &mpsc::UnboundedSender<Message>, value: &Value) -> bool {
    tx.send(Message::text(value.to_string())).is_ok()
}

fn render_error(res: &mut Response, status_code: StatusCode, code: &str, message: &str) {
    res.status_code(status_code);
    res.render(Json(
        json!({ "error": { "code": code, "message": message } }),
    ));
}

#[handler]
async fn healthz(res: &mut Response) {
    res.render(Text::Plain("ok"));
}

#[handler]
async fn status(res: &mut Response) {
    let app = state();
    let inner = app.inner.lock().await;
    let active_session = inner
        .sessions
        .values()
        .find(|session| session.state != "closed")
        .map(|session| json!({ "id": session.id, "state": session.state }));
    res.render(Json(json!({
        "service": "agora-sensevoice-control-plane",
        "bridgeOnline": inner.bridge.is_some(),
        "demoMode": app.config.demo_mode,
        "activeSession": active_session,
    })));
}

#[handler]
async fn create_session(res: &mut Response) {
    let app = state();
    let mut inner = app.inner.lock().await;

    if inner.bridge.is_none() {
        render_error(
            res,
            StatusCode::SERVICE_UNAVAILABLE,
            "bridge_offline",
            "The LAN bridge is not connected",
        );
        return;
    }
    if inner
        .sessions
        .values()
        .any(|session| session.state != "closed")
    {
        render_error(
            res,
            StatusCode::CONFLICT,
            "session_busy",
            "This demo supports one active session at a time",
        );
        return;
    }

    let now = unix_ms();
    let session = Session {
        id: Uuid::new_v4().to_string(),
        ticket: Uuid::new_v4().to_string(),
        channel: app.config.channel.clone(),
        client_uid: app.config.client_uid,
        bridge_uid: app.config.bridge_uid,
        state: "starting".into(),
        created_at_ms: now,
        expires_at_ms: now + app.config.session_ttl_seconds * 1000,
    };

    let start_event = json!({
        "type": "session.start",
        "sessionId": session.id,
        "agora": {
            "appId": app.config.agora_app_id,
            "channel": session.channel,
            "uid": session.bridge_uid,
            "token": app.config.bridge_rtc_token,
        }
    });
    let bridge_sent = inner
        .bridge
        .as_ref()
        .is_some_and(|bridge| send_json(&bridge.tx, &start_event));
    if !bridge_sent {
        inner.bridge = None;
        render_error(
            res,
            StatusCode::SERVICE_UNAVAILABLE,
            "bridge_offline",
            "The LAN bridge disconnected while starting the session",
        );
        return;
    }

    let response = json!({
        "sessionId": session.id,
        "ticket": session.ticket,
        "state": session.state,
        "expiresAtMs": session.expires_at_ms,
        "eventsWsPath": format!("/ws/client/{}?ticket={}", session.id, session.ticket),
        "demoMode": app.config.demo_mode,
        "agora": {
            "appId": app.config.agora_app_id,
            "channel": session.channel,
            "uid": session.client_uid,
            "token": app.config.client_rtc_token,
        }
    });
    info!(session_id = %session.id, "session created");
    inner.sessions.insert(session.id.clone(), session);
    res.status_code(StatusCode::CREATED);
    res.render(Json(response));
}

#[handler]
async fn commit_utterance(req: &mut Request, res: &mut Response) {
    forward_session_command(req, res, "utterance.commit").await;
}

#[handler]
async fn delete_session(req: &mut Request, res: &mut Response) {
    let Some(id) = req.param::<String>("id") else {
        render_error(
            res,
            StatusCode::BAD_REQUEST,
            "bad_session_id",
            "Missing session id",
        );
        return;
    };
    let app = state();
    let mut inner = app.inner.lock().await;
    let Some(session) = inner.sessions.get_mut(&id) else {
        render_error(
            res,
            StatusCode::NOT_FOUND,
            "session_not_found",
            "Session not found",
        );
        return;
    };
    session.state = "closed".into();
    if let Some(bridge) = &inner.bridge {
        send_json(
            &bridge.tx,
            &json!({ "type": "session.stop", "sessionId": id }),
        );
    }
    if let Some(client) = inner.clients.remove(&id) {
        send_json(
            &client.tx,
            &json!({ "type": "session.closed", "sessionId": id }),
        );
    }
    info!(session_id = %id, "session stopped");
    res.status_code(StatusCode::NO_CONTENT);
}

async fn forward_session_command(req: &mut Request, res: &mut Response, event_type: &str) {
    let Some(id) = req.param::<String>("id") else {
        render_error(
            res,
            StatusCode::BAD_REQUEST,
            "bad_session_id",
            "Missing session id",
        );
        return;
    };
    let app = state();
    let inner = app.inner.lock().await;
    if !inner.sessions.contains_key(&id) {
        render_error(
            res,
            StatusCode::NOT_FOUND,
            "session_not_found",
            "Session not found",
        );
        return;
    }
    let sent = inner.bridge.as_ref().is_some_and(|bridge| {
        send_json(&bridge.tx, &json!({ "type": event_type, "sessionId": id }))
    });
    if !sent {
        render_error(
            res,
            StatusCode::SERVICE_UNAVAILABLE,
            "bridge_offline",
            "The LAN bridge is not connected",
        );
        return;
    }
    res.status_code(StatusCode::ACCEPTED);
    res.render(Json(json!({ "accepted": true })));
}

fn origin_allowed(req: &Request, config: &Config) -> bool {
    let Some(expected) = &config.allowed_origin else {
        return true;
    };
    req.headers()
        .get(header::ORIGIN)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|origin| origin == expected)
}

#[handler]
async fn bridge_ws(req: &mut Request, res: &mut Response) {
    let app = state().clone();
    let expected = format!("Bearer {}", app.config.bridge_shared_secret);
    let authorized = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value == expected);
    if !authorized {
        render_error(
            res,
            StatusCode::UNAUTHORIZED,
            "unauthorized",
            "Invalid bridge secret",
        );
        return;
    }
    let app_for_socket = app.clone();
    if let Err(error) = WebSocketUpgrade::new()
        .upgrade(req, res, move |socket| {
            bridge_socket(socket, app_for_socket)
        })
        .await
    {
        error!(%error, "bridge websocket upgrade failed");
    }
}

async fn bridge_socket(mut socket: WebSocket, app: Arc<AppState>) {
    let socket_id = Uuid::new_v4();
    let (tx, mut rx) = mpsc::unbounded_channel();
    {
        let mut inner = app.inner.lock().await;
        if inner
            .bridge
            .replace(SocketLink { id: socket_id, tx })
            .is_some()
        {
            warn!("replaced an existing bridge connection");
        }
    }
    info!(bridge_connection = %socket_id, "bridge connected");

    loop {
        tokio::select! {
            outbound = rx.recv() => {
                let Some(message) = outbound else { break; };
                if socket.send(message).await.is_err() { break; }
            }
            inbound = socket.recv() => {
                match inbound {
                    Some(Ok(message)) if message.is_text() => {
                        if let Ok(text) = message.as_str() {
                            handle_bridge_event(&app, text).await;
                        }
                    }
                    Some(Ok(message)) if message.is_close() => break,
                    None => break,
                    Some(Ok(_)) => {}
                    Some(Err(error)) => {
                        warn!(%error, "bridge websocket receive error");
                        break;
                    }
                }
            }
        }
    }

    let mut inner = app.inner.lock().await;
    if inner
        .bridge
        .as_ref()
        .is_some_and(|link| link.id == socket_id)
    {
        inner.bridge = None;
    }
    info!(bridge_connection = %socket_id, "bridge disconnected");
}

async fn handle_bridge_event(app: &Arc<AppState>, text: &str) {
    let event: Value = match serde_json::from_str(text) {
        Ok(event) => event,
        Err(error) => {
            warn!(%error, "ignored invalid bridge message");
            return;
        }
    };
    let Some(event_type) = event.get("type").and_then(Value::as_str) else {
        warn!("ignored bridge message without type");
        return;
    };
    let Some(session_id) = event.get("sessionId").and_then(Value::as_str) else {
        warn!(event_type, "ignored bridge message without sessionId");
        return;
    };
    let mut inner = app.inner.lock().await;
    let Some(session) = inner.sessions.get_mut(session_id) else {
        warn!(session_id, event_type, "ignored event for unknown session");
        return;
    };
    match event_type {
        "session.ready" => session.state = "ready".into(),
        "asr.error" => session.state = "error".into(),
        "session.closed" => session.state = "closed".into(),
        "asr.partial" | "asr.final" => {}
        _ => {
            warn!(session_id, event_type, "ignored unknown bridge event");
            return;
        }
    }
    if let Some(client) = inner.clients.get(session_id) {
        send_json(&client.tx, &event);
    }
}

#[handler]
async fn client_ws(req: &mut Request, res: &mut Response) {
    let app = state().clone();
    if !origin_allowed(req, &app.config) {
        render_error(
            res,
            StatusCode::FORBIDDEN,
            "origin_forbidden",
            "Origin is not allowed",
        );
        return;
    }
    let Some(session_id) = req.param::<String>("id") else {
        render_error(
            res,
            StatusCode::BAD_REQUEST,
            "bad_session_id",
            "Missing session id",
        );
        return;
    };
    let ticket = req.query::<String>("ticket").unwrap_or_default();
    {
        let inner = app.inner.lock().await;
        let valid = inner
            .sessions
            .get(&session_id)
            .is_some_and(|session| session.ticket == ticket && session.state != "closed");
        if !valid {
            render_error(
                res,
                StatusCode::UNAUTHORIZED,
                "invalid_ticket",
                "Invalid session ticket",
            );
            return;
        }
    }
    let app_for_socket = app.clone();
    if let Err(error) = WebSocketUpgrade::new()
        .upgrade(req, res, move |socket| {
            client_socket(socket, app_for_socket, session_id)
        })
        .await
    {
        error!(%error, "client websocket upgrade failed");
    }
}

async fn client_socket(mut socket: WebSocket, app: Arc<AppState>, session_id: String) {
    let socket_id = Uuid::new_v4();
    let (tx, mut rx) = mpsc::unbounded_channel();
    {
        let mut inner = app.inner.lock().await;
        let state_value = inner
            .sessions
            .get(&session_id)
            .map(|session| session.state.clone())
            .unwrap_or_else(|| "closed".into());
        send_json(
            &tx,
            &json!({
                "type": "session.snapshot",
                "sessionId": session_id,
                "state": state_value,
                "bridgeOnline": inner.bridge.is_some(),
            }),
        );
        inner
            .clients
            .insert(session_id.clone(), SocketLink { id: socket_id, tx });
    }

    loop {
        tokio::select! {
            outbound = rx.recv() => {
                let Some(message) = outbound else { break; };
                if socket.send(message).await.is_err() { break; }
            }
            inbound = socket.recv() => {
                match inbound {
                    Some(Ok(message)) if message.is_close() => break,
                    None => break,
                    Some(Err(error)) => {
                        warn!(%error, session_id, "client websocket receive error");
                        break;
                    }
                    _ => {}
                }
            }
        }
    }

    let mut inner = app.inner.lock().await;
    if inner
        .clients
        .get(&session_id)
        .is_some_and(|link| link.id == socket_id)
    {
        inner.clients.remove(&session_id);
    }
}

#[handler]
async fn security_headers(
    req: &mut Request,
    depot: &mut Depot,
    res: &mut Response,
    ctrl: &mut FlowCtrl,
) {
    ctrl.call_next(req, depot, res).await;
    let headers = res.headers_mut();
    headers.insert(
        header::X_CONTENT_TYPE_OPTIONS,
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("same-origin"),
    );
    headers.insert(header::X_FRAME_OPTIONS, HeaderValue::from_static("DENY"));
}

#[tokio::main]
async fn main() {
    if env::args().any(|argument| argument == "--healthcheck") {
        if let Err(error) = run_healthcheck() {
            eprintln!("healthcheck failed: {error}");
            std::process::exit(1);
        }
        return;
    }
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "agora_sensevoice_control_plane=info,salvo=info".into()),
        )
        .init();

    let config = Config::from_env().unwrap_or_else(|error| {
        eprintln!("configuration error: {error}");
        std::process::exit(2);
    });
    let bind_addr = config.bind_addr.clone();
    let static_dir = config.static_dir.clone();
    info!(
        bind_addr,
        public_base_url = %config.public_base_url,
        demo_mode = config.demo_mode,
        "starting control plane"
    );
    APP_STATE
        .set(Arc::new(AppState::new(config)))
        .unwrap_or_else(|_| panic!("application state already initialized"));

    let api = Router::with_path("api/v1")
        .push(Router::with_path("status").get(status))
        .push(Router::with_path("sessions").post(create_session))
        .push(
            Router::with_path("sessions/{id}")
                .delete(delete_session)
                .push(Router::with_path("commit").post(commit_utterance)),
        );
    let router = Router::new()
        .hoop(Logger::new())
        .hoop(CatchPanic::new())
        .hoop(security_headers)
        .push(Router::with_path("healthz").get(healthz))
        .push(api)
        .push(Router::with_path("ws/bridge").get(bridge_ws))
        .push(Router::with_path("ws/client/{id}").get(client_ws))
        .push(
            Router::with_path("{**path}").get(StaticDir::new([static_dir]).defaults("index.html")),
        );

    let acceptor = TcpListener::new(bind_addr).bind().await;
    Server::new(acceptor).serve(router).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_config() -> Config {
        Config {
            bind_addr: "127.0.0.1:0".into(),
            static_dir: "static".into(),
            public_base_url: "http://localhost".into(),
            allowed_origin: None,
            agora_app_id: String::new(),
            channel: "demo".into(),
            client_uid: 1001,
            bridge_uid: 9001,
            client_rtc_token: String::new(),
            bridge_rtc_token: String::new(),
            bridge_shared_secret: "0123456789abcdef".into(),
            session_ttl_seconds: 900,
            demo_mode: true,
        }
    }

    #[test]
    fn mock_mode_does_not_require_agora_credentials() {
        assert!(base_config().validate().is_ok());
    }

    #[test]
    fn real_mode_requires_agora_credentials() {
        let mut config = base_config();
        config.demo_mode = false;
        assert!(config.validate().is_err());
    }

    #[test]
    fn uids_must_be_unique() {
        let mut config = base_config();
        config.bridge_uid = config.client_uid;
        assert!(config.validate().is_err());
    }
}
