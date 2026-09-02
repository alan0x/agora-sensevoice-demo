//! Minimal Agora AccessToken2 (`007`) RTC token builder.
//!
//! The wire format and signing order are ported from Agora's official Tools
//! repository:
//! <https://github.com/AgoraIO/Tools/tree/master/DynamicKey/AgoraDynamicKey/go/src/accesstoken2>
//! Keeping the implementation local avoids sending the App Certificate
//! anywhere outside this control plane.

use std::{
    io::Write,
    time::{SystemTime, UNIX_EPOCH},
};

use base64::{Engine as _, engine::general_purpose::STANDARD};
use flate2::{Compression, write::ZlibEncoder};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use uuid::Uuid;

type HmacSha256 = Hmac<Sha256>;

const VERSION: &str = "007";
const SERVICE_TYPE_RTC: u16 = 1;
const PRIVILEGE_JOIN_CHANNEL: u16 = 1;
const PRIVILEGE_PUBLISH_AUDIO: u16 = 2;

#[derive(Clone, Copy, Debug)]
pub enum RtcRole {
    AudioPublisher,
    Subscriber,
}

pub fn validate_credential(name: &str, value: &str) -> Result<(), String> {
    if value.len() != 32 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(format!("{name} must be a 32-character hexadecimal value"));
    }
    Ok(())
}

pub fn build_rtc_token(
    app_id: &str,
    app_certificate: &str,
    channel: &str,
    uid: u32,
    role: RtcRole,
    expires_in_seconds: u32,
) -> Result<String, String> {
    let issue_timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|error| format!("system clock is before UNIX epoch: {error}"))?
        .as_secs()
        .try_into()
        .map_err(|_| "system timestamp exceeds Agora token range".to_owned())?;
    let random = Uuid::new_v4();
    let salt =
        u32::from_le_bytes(random.as_bytes()[..4].try_into().expect("four bytes")) % 99_999_998 + 1;

    build_rtc_token_at(
        app_id,
        app_certificate,
        channel,
        uid,
        role,
        expires_in_seconds,
        issue_timestamp,
        salt,
    )
}

#[allow(clippy::too_many_arguments)]
fn build_rtc_token_at(
    app_id: &str,
    app_certificate: &str,
    channel: &str,
    uid: u32,
    role: RtcRole,
    expires_in_seconds: u32,
    issue_timestamp: u32,
    salt: u32,
) -> Result<String, String> {
    validate_credential("AGORA_APP_ID", app_id)?;
    validate_credential("AGORA_APP_CERTIFICATE", app_certificate)?;
    if channel.is_empty() || channel.len() >= 64 {
        return Err("Agora channel must contain between 1 and 63 bytes".into());
    }
    if uid == 0 {
        return Err("Agora UID must be non-zero".into());
    }
    if expires_in_seconds == 0 || expires_in_seconds > 86_400 {
        return Err("Agora token lifetime must be between 1 and 86400 seconds".into());
    }

    let mut signing_info = Vec::with_capacity(128);
    pack_string(&mut signing_info, app_id)?;
    pack_u32(&mut signing_info, issue_timestamp);
    pack_u32(&mut signing_info, expires_in_seconds);
    pack_u32(&mut signing_info, salt);
    pack_u16(&mut signing_info, 1); // one RTC service

    pack_u16(&mut signing_info, SERVICE_TYPE_RTC);
    let privileges: &[(u16, u32)] = match role {
        RtcRole::AudioPublisher => &[
            (PRIVILEGE_JOIN_CHANNEL, expires_in_seconds),
            (PRIVILEGE_PUBLISH_AUDIO, expires_in_seconds),
        ],
        RtcRole::Subscriber => &[(PRIVILEGE_JOIN_CHANNEL, expires_in_seconds)],
    };
    pack_u16(&mut signing_info, privileges.len() as u16);
    for (privilege, expires) in privileges {
        pack_u16(&mut signing_info, *privilege);
        pack_u32(&mut signing_info, *expires);
    }
    pack_string(&mut signing_info, channel)?;
    pack_string(&mut signing_info, &uid.to_string())?;

    // AccessToken2 intentionally uses the packed timestamp and salt as the
    // HMAC keys, matching Agora's official implementation.
    let issue_key = hmac(
        issue_timestamp.to_le_bytes().as_slice(),
        app_certificate.as_bytes(),
    )?;
    let signing_key = hmac(salt.to_le_bytes().as_slice(), &issue_key)?;
    let signature = hmac(&signing_key, &signing_info)?;

    let mut content = Vec::with_capacity(signing_info.len() + signature.len() + 2);
    pack_bytes(&mut content, &signature)?;
    content.extend_from_slice(&signing_info);

    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(&content)
        .map_err(|error| format!("compress Agora token: {error}"))?;
    let compressed = encoder
        .finish()
        .map_err(|error| format!("finish Agora token compression: {error}"))?;
    Ok(format!("{VERSION}{}", STANDARD.encode(compressed)))
}

fn hmac(key: &[u8], value: &[u8]) -> Result<Vec<u8>, String> {
    let mut mac = HmacSha256::new_from_slice(key)
        .map_err(|_| "invalid HMAC key while building Agora token".to_owned())?;
    mac.update(value);
    Ok(mac.finalize().into_bytes().to_vec())
}

fn pack_u16(buffer: &mut Vec<u8>, value: u16) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn pack_u32(buffer: &mut Vec<u8>, value: u32) {
    buffer.extend_from_slice(&value.to_le_bytes());
}

fn pack_string(buffer: &mut Vec<u8>, value: &str) -> Result<(), String> {
    pack_bytes(buffer, value.as_bytes())
}

fn pack_bytes(buffer: &mut Vec<u8>, value: &[u8]) -> Result<(), String> {
    let length: u16 = value
        .len()
        .try_into()
        .map_err(|_| "Agora token field exceeds 65535 bytes".to_owned())?;
    pack_u16(buffer, length);
    buffer.extend_from_slice(value);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const APP_ID: &str = "970CA35de60c44645bbae8a215061b33";
    const APP_CERTIFICATE: &str = "5CFd2fd1755d40ecb72977518be15d3b";
    const CHANNEL: &str = "7d72365eb983485397e3e3f9d460bdda";
    const UID: u32 = 2_882_341_273;

    #[test]
    fn subscriber_payload_matches_official_agora_go_vector() {
        let token = build_rtc_token_at(
            APP_ID,
            APP_CERTIFICATE,
            CHANNEL,
            UID,
            RtcRole::Subscriber,
            600,
            1_111_111,
            1,
        )
        .unwrap();
        let official = "007eJxSYBBbsMMnKq7p9Hf/HcIX5kce9b518kCiQgSr5Zrp4X1Tu6UUGCzNDZwdjU1TUs0Mkk1MzExMk5ISUy0SjQxNDcwMk4yN3b8IMEQwMTAwMoAwBIL4CgzmKeZGxmamqUmWFsYmFqbGluapxqnGaZYpJmYGSSkpiVwMRhYWRsYmhkbmxoAAAAD//8JqJOM=";

        assert_eq!(decode_payload(&token), decode_payload(official));
    }

    #[test]
    fn publisher_token_is_distinct_and_versioned() {
        let publisher = build_rtc_token_at(
            APP_ID,
            APP_CERTIFICATE,
            CHANNEL,
            UID,
            RtcRole::AudioPublisher,
            600,
            1_111_111,
            1,
        )
        .unwrap();
        let subscriber = build_rtc_token_at(
            APP_ID,
            APP_CERTIFICATE,
            CHANNEL,
            UID,
            RtcRole::Subscriber,
            600,
            1_111_111,
            1,
        )
        .unwrap();

        assert!(publisher.starts_with(VERSION));
        assert_ne!(publisher, subscriber);
    }

    #[test]
    fn credentials_and_lifetime_are_validated() {
        assert!(validate_credential("id", APP_ID).is_ok());
        assert!(validate_credential("id", "not-a-credential").is_err());
        assert!(
            build_rtc_token_at(
                APP_ID,
                APP_CERTIFICATE,
                CHANNEL,
                UID,
                RtcRole::Subscriber,
                86_401,
                1,
                1,
            )
            .is_err()
        );
    }

    fn decode_payload(token: &str) -> Vec<u8> {
        use std::io::Read;

        use flate2::read::ZlibDecoder;

        let compressed = STANDARD.decode(&token[VERSION.len()..]).unwrap();
        let mut decoder = ZlibDecoder::new(compressed.as_slice());
        let mut payload = Vec::new();
        decoder.read_to_end(&mut payload).unwrap();
        payload
    }
}
