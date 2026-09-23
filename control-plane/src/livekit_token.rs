//! Minimal LiveKit JWT (HS256) builder.
//!
//! LiveKit access tokens are plain signed JWTs carrying participant grants:
//! <https://docs.livekit.io/home/get-started/authentication/>
//! Hand-rolling keeps the dependency tree identical to the Agora token module.

use std::time::{SystemTime, UNIX_EPOCH};

use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use hmac::{Hmac, Mac};
use serde_json::json;
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

pub fn validate_livekit_url(value: &str) -> Result<(), String> {
    if !(value.starts_with("wss://") || value.starts_with("ws://")) {
        return Err("LIVEKIT_URL must start with wss:// or ws://".to_owned());
    }
    if value.trim_end_matches('/').len() < 10 {
        return Err("LIVEKIT_URL must include a host".to_owned());
    }
    Ok(())
}

pub fn build_livekit_token(
    api_key: &str,
    api_secret: &str,
    identity: &str,
    room: &str,
    can_publish: bool,
    ttl_seconds: u32,
) -> Result<String, String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("system clock is before UNIX epoch: {error}"))?
        .as_secs();
    let header = URL_SAFE_NO_PAD.encode(json!({ "alg": "HS256", "typ": "JWT" }).to_string());
    let payload = URL_SAFE_NO_PAD.encode(
        json!({
            "iss": api_key,
            "sub": identity,
            "iat": now,
            "nbf": now.saturating_sub(10),
            "exp": now + u64::from(ttl_seconds),
            "video": {
                "roomJoin": true,
                "room": room,
                "canPublish": can_publish,
                "canSubscribe": true,
            },
        })
        .to_string(),
    );
    let signing_input = format!("{header}.{payload}");
    let mut mac = HmacSha256::new_from_slice(api_secret.as_bytes())
        .map_err(|error| format!("invalid LiveKit API secret: {error}"))?;
    mac.update(signing_input.as_bytes());
    let signature = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    Ok(format!("{signing_input}.{signature}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decode_segment(segment: &str) -> serde_json::Value {
        let bytes = URL_SAFE_NO_PAD
            .decode(segment.as_bytes())
            .expect("base64url segment");
        serde_json::from_slice(&bytes).expect("json segment")
    }

    #[test]
    fn token_carries_identity_room_and_grants() {
        let token = build_livekit_token(
            "APIkey123",
            "secret-secret-secret",
            "client-1001",
            "asr-room-1",
            true,
            1200,
        )
        .expect("token builds");
        let parts: Vec<&str> = token.split('.').collect();
        assert_eq!(parts.len(), 3);

        let header = decode_segment(parts[0]);
        assert_eq!(header["alg"], "HS256");

        let payload = decode_segment(parts[1]);
        assert_eq!(payload["iss"], "APIkey123");
        assert_eq!(payload["sub"], "client-1001");
        assert_eq!(payload["video"]["room"], "asr-room-1");
        assert_eq!(payload["video"]["roomJoin"], true);
        assert_eq!(payload["video"]["canPublish"], true);
        assert_eq!(payload["video"]["canSubscribe"], true);

        let mut mac = HmacSha256::new_from_slice(b"secret-secret-secret").expect("hmac");
        mac.update(format!("{}.{}", parts[0], parts[1]).as_bytes());
        let expected = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
        assert_eq!(parts[2], expected, "signature must verify");
    }

    #[test]
    fn subscriber_token_cannot_publish() {
        let token =
            build_livekit_token("k", "s", "bridge-9001", "room-x", false, 60).expect("token");
        let payload = decode_segment(token.split('.').nth(1).unwrap());
        assert_eq!(payload["video"]["canPublish"], false);
    }

    #[test]
    fn url_validation() {
        assert!(validate_livekit_url("wss://rtc.example.com:9443").is_ok());
        assert!(validate_livekit_url("ws://127.0.0.1:7880").is_ok());
        assert!(validate_livekit_url("https://rtc.example.com").is_err());
        assert!(validate_livekit_url("wss://").is_err());
    }
}
