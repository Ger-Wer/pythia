use std::collections::HashSet;
use std::sync::Arc;

use anyhow::{anyhow, Result};
use bytes::Bytes;
use dual_loop_common::bus::ConsumerMessage;
use dual_loop_common::proto::common::{MarketId, Uuid};
use dual_loop_common::proto::control::{KillAck, KillAckStatus, KillCommand, KillScope};
use dual_loop_common::proto::{new_uuid_v7, now_unix_ms};
use hex::encode as hex_encode;
use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;
use tokio::sync::RwLock;
use uuid::Uuid as ExternalUuid;

const CLOCK_SKEW_MS: i64 = 5_000;
const DEFAULT_HMAC_SECRET: &str = "dev-kill-hmac-secret-change-me";

type HmacSha256 = Hmac<Sha256>;

#[derive(Debug, Clone)]
pub struct ActiveKill {
    pub original_command_id: Uuid,
    pub scope: KillScope,
    pub market: Option<MarketId>,
    pub final_ack_sent: bool,
}

impl ActiveKill {
    pub fn blocks_market(&self, market: Option<&MarketId>) -> bool {
        match self.scope {
            KillScope::Unspecified | KillScope::Global | KillScope::NewTriggers => true,
            KillScope::Market => {
                let Some(target) = self.market.as_ref() else {
                    return true;
                };
                market
                    .map(|candidate| candidate.polymarket_id == target.polymarket_id)
                    .unwrap_or(false)
            }
        }
    }
}

pub type SharedKillState = Arc<RwLock<Option<ActiveKill>>>;

#[derive(Debug, Clone)]
pub struct ControlConfig {
    pub hmac_secret: String,
    pub consumer_name: String,
}

impl ControlConfig {
    pub fn from_env() -> Self {
        Self {
            hmac_secret: std::env::var("KILL_HMAC_SECRET")
                .unwrap_or_else(|_| DEFAULT_HMAC_SECRET.to_string()),
            consumer_name: format!("sniper-executor-{}", std::process::id()),
        }
    }
}

#[derive(Debug, Deserialize)]
struct LegacyKillEnvelope {
    message_id: String,
    operator_id: String,
    scope: String,
    market_id: Option<String>,
    nonce: u64,
    issued_at_unix_ms: i64,
    signature: String,
}

pub fn new_shared_state() -> SharedKillState {
    Arc::new(RwLock::new(None))
}

pub async fn engaged(state: &SharedKillState) -> bool {
    state.read().await.is_some()
}

pub async fn blocks_market(state: &SharedKillState, market: Option<&MarketId>) -> bool {
    state
        .read()
        .await
        .as_ref()
        .map(|kill| kill.blocks_market(market))
        .unwrap_or(false)
}

pub async fn engage(state: &SharedKillState, command: &KillCommand, in_flight: usize) -> KillAck {
    let scope = scope_from_i32(command.scope);
    let status = if in_flight > 0 {
        KillAckStatus::Draining
    } else {
        KillAckStatus::Complete
    };

    let Some(original_command_id) = command.message_id.clone() else {
        return ack_for(
            None,
            KillScope::Unspecified,
            KillAckStatus::RejectedBadSignature,
            "kill command missing message_id",
        );
    };

    {
        let mut guard = state.write().await;
        *guard = Some(ActiveKill {
            original_command_id: original_command_id.clone(),
            scope,
            market: command.market.clone(),
            final_ack_sent: matches!(status, KillAckStatus::Complete),
        });
    }

    let detail = if in_flight > 0 {
        format!("kill engaged; draining {in_flight} in-flight execution(s)")
    } else {
        "kill engaged; no in-flight executions".to_string()
    };

    ack_for(Some(&original_command_id), scope, status, detail)
}

pub async fn complete_if_drained(state: &SharedKillState) -> Option<KillAck> {
    let mut guard = state.write().await;
    let active = guard.as_mut()?;
    if active.final_ack_sent {
        return None;
    }

    active.final_ack_sent = true;
    Some(ack_for(
        Some(&active.original_command_id),
        active.scope,
        KillAckStatus::Complete,
        "kill drain complete",
    ))
}

pub fn decode_kill_command(message: &ConsumerMessage) -> Result<KillCommand> {
    if let Ok(command) = message.decode::<KillCommand>() {
        return Ok(command);
    }

    let legacy: LegacyKillEnvelope = serde_json::from_slice(&message.payload)?;
    let message_id = uuid_from_string(&legacy.message_id)?;
    let scope = scope_from_string(&legacy.scope);
    let market = match legacy.market_id {
        Some(market_id) if !market_id.trim().is_empty() => Some(MarketId {
            polymarket_id: market_id,
            ctf_address: String::new(),
        }),
        _ => None,
    };
    let signature = hex::decode(legacy.signature.trim())
        .map(Bytes::from)
        .map_err(|error| anyhow!("legacy kill signature is not valid hex: {error}"))?;

    Ok(KillCommand {
        message_id: Some(message_id),
        operator_id: legacy.operator_id,
        scope: scope as i32,
        market,
        nonce: legacy.nonce,
        issued_at_unix_ms: legacy.issued_at_unix_ms,
        signature,
    })
}

pub fn validate_kill_command(
    command: &KillCommand,
    hmac_secret: &str,
    seen_nonces: &mut HashSet<u64>,
    now_ms: i64,
) -> Result<(), KillAck> {
    let Some(command_id) = command.message_id.as_ref() else {
        return Err(ack_for(
            None,
            scope_from_i32(command.scope),
            KillAckStatus::RejectedBadSignature,
            "kill command missing message_id",
        ));
    };

    if (now_ms - command.issued_at_unix_ms).abs() > CLOCK_SKEW_MS {
        return Err(ack_for(
            Some(command_id),
            scope_from_i32(command.scope),
            KillAckStatus::RejectedStale,
            "kill command outside allowed clock skew window",
        ));
    }

    if !verify_signature(command, hmac_secret) {
        return Err(ack_for(
            Some(command_id),
            scope_from_i32(command.scope),
            KillAckStatus::RejectedBadSignature,
            "kill command signature verification failed",
        ));
    }

    if !seen_nonces.insert(command.nonce) {
        return Err(ack_for(
            Some(command_id),
            scope_from_i32(command.scope),
            KillAckStatus::RejectedReplay,
            "kill command nonce already seen",
        ));
    }

    Ok(())
}

fn ack_for(
    original_command_id: Option<&Uuid>,
    scope: KillScope,
    status: KillAckStatus,
    detail: impl Into<String>,
) -> KillAck {
    KillAck {
        message_id: Some(new_uuid_v7()),
        original_command_id: original_command_id.cloned(),
        scope: scope as i32,
        status: status as i32,
        detail: detail.into(),
        active_triggers_cancelled: 0,
        pending_tx_cancelled: 0,
        ack_at_unix_ms: now_unix_ms(),
    }
}

fn verify_signature(command: &KillCommand, secret: &str) -> bool {
    let Some(message_id) = command.message_id.as_ref() else {
        return false;
    };
    let Ok(message_id) = uuid_to_string(message_id) else {
        return false;
    };

    let mut mac = match HmacSha256::new_from_slice(secret.as_bytes()) {
        Ok(mac) => mac,
        Err(_) => return false,
    };
    let payload = [
        message_id,
        command.operator_id.clone(),
        scope_label(scope_from_i32(command.scope)).to_string(),
        command
            .market
            .as_ref()
            .map(|market| market.polymarket_id.clone())
            .unwrap_or_default(),
        command.nonce.to_string(),
        command.issued_at_unix_ms.to_string(),
    ]
    .join("|");
    mac.update(payload.as_bytes());
    let expected = mac.finalize().into_bytes();
    let expected_bytes = &expected[..];
    let expected_hex = hex_encode(expected_bytes);
    let provided = command.signature.as_ref();

    provided == expected_bytes || provided.eq_ignore_ascii_case(expected_hex.as_bytes())
}

fn scope_from_i32(value: i32) -> KillScope {
    KillScope::try_from(value).unwrap_or(KillScope::Unspecified)
}

fn scope_from_string(value: &str) -> KillScope {
    match value.trim().to_ascii_uppercase().as_str() {
        "GLOBAL" => KillScope::Global,
        "MARKET" => KillScope::Market,
        "NEW_TRIGGERS" => KillScope::NewTriggers,
        _ => KillScope::Unspecified,
    }
}

fn scope_label(scope: KillScope) -> &'static str {
    match scope {
        KillScope::Unspecified => "UNSPECIFIED",
        KillScope::Global => "GLOBAL",
        KillScope::Market => "MARKET",
        KillScope::NewTriggers => "NEW_TRIGGERS",
    }
}

fn uuid_from_string(value: &str) -> Result<Uuid> {
    let parsed = ExternalUuid::parse_str(value)?;
    Ok(Uuid {
        value: Bytes::copy_from_slice(parsed.as_bytes()),
    })
}

fn uuid_to_string(value: &Uuid) -> Result<String> {
    let parsed = ExternalUuid::from_slice(value.value.as_ref())?;
    Ok(parsed.hyphenated().to_string())
}
