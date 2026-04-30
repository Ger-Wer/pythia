/**
 * Cross-language contract test helper.
 *
 * Publishes one deterministic `WatchlistUpsert` to Redis Streams and exits.
 * Paired with `services/common/tests/cross_lang.rs` on the consume side.
 *
 * Usage:
 *   REDIS_URL=redis://127.0.0.1:6379 bun run scripts/contract/publish_watchlist_upsert.ts
 */

import { StreamPublisher } from '../../src/services/bus/redis_streams';
import { WATCHLIST_UPSERT } from '../../src/services/bus/topics';
import { Side } from '../../src/generated/dual_loop/v1/common';
import {
  TriggerKind,
  WatchlistUpsert,
} from '../../src/generated/dual_loop/v1/watchlist';

async function main() {
  const url = process.env.REDIS_URL ?? 'redis://127.0.0.1:6379';
  const publisher = new StreamPublisher({ url });

  const msg: WatchlistUpsert = {
    messageId: { value: Buffer.alloc(16, 0x11) },
    market: {
      polymarketId: 'cross-lang-001',
      ctfAddress: '0xfeed',
      marketSpecificKey:
        '0xcrosslang000000000000000000000000000000000000000000000000000000',
    },
    version: 7n,
    edgePpm: 42_000n,
    pFundamentalPpm: 700_000n,
    confidencePpm: 950_000n,
    sideHint: Side.SIDE_YES,
    maxPositionUsd: { amountMicros: 5_000_000n },
    maxSlippageBps: 25,
    ttlMs: 60_000n,
    issuedAtUnixMs: 1_700_000_000_000n,
    triggerPolicy: {
      minIndependentSources: 2,
      allowedKinds: [TriggerKind.TRIGGER_KIND_LARGE_FILL],
      maxPriceImpactBps: 100,
    },
  };

  const id = await publisher.publish(
    WATCHLIST_UPSERT,
    WatchlistUpsert,
    msg,
    'cross-lang-test',
  );
  // eslint-disable-next-line no-console
  console.log(JSON.stringify({ stream_id: id, topic: WATCHLIST_UPSERT }));
  await publisher.close();
}

main().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});
