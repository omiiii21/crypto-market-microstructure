"""
Integration test for real exchange connections.

This script connects to actual Binance and OKX WebSocket endpoints
to verify our adapters work with live market data.

Usage:
    python tests/integration/test_live_connections.py

Expected Output:
    - Real-time BTC-USDT order book data from Binance
    - Real-time BTC-USDT order book data from OKX
    - Sequence IDs being tracked
    - No gaps detected (in normal conditions)
    - Clean connection and disconnection
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.adapters.binance import BinanceAdapter
from src.adapters.okx import OKXAdapter
from src.config.loader import load_config


async def test_binance_connection():
    """Test real Binance WebSocket connection."""
    print("\n" + "=" * 80)
    print("🔵 BINANCE INTEGRATION TEST")
    print("=" * 80)

    try:
        # Load configuration
        config = load_config()
        exchange_config = config.get_exchange("binance")
        instruments = [config.get_instrument("BTC-USDT-PERP")]

        if not exchange_config or not instruments[0]:
            print("❌ Configuration not found. Check config/exchanges.yaml and config/instruments.yaml")
            return False

        # Create adapter
        adapter = BinanceAdapter(exchange_config, instruments)
        print("✅ Binance adapter created")

        # Connect
        print("🔌 Connecting to Binance WebSocket...")
        await adapter.connect()
        print("✅ Connected to Binance!")

        # Check health before subscribing
        health = await adapter.health_check()
        print(f"📊 Initial Health: {health.status.value}, Connected: {adapter.is_connected}")

        # Subscribe
        print("📡 Subscribing to BTC-USDT-PERP...")
        await adapter.subscribe(["BTC-USDT-PERP"])
        print("✅ Subscribed!")

        # Stream data
        print("\n📈 Streaming live order book data (10 snapshots)...")
        print("-" * 80)

        count = 0
        prev_seq = None

        async for snapshot in adapter.stream_order_books():
            count += 1

            # Display snapshot info
            print(f"\n[{count}] {snapshot.instrument} @ {snapshot.timestamp.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"    Best Bid: {snapshot.best_bid:,.2f} USDT")
            print(f"    Best Ask: {snapshot.best_ask:,.2f} USDT")
            print(f"    Spread:   {snapshot.spread_bps:.2f} bps")
            print(f"    Sequence: {snapshot.sequence_id}")

            # Check for gaps
            if prev_seq is not None:
                gap = adapter.detect_gap(prev_seq, snapshot.sequence_id)
                if gap:
                    print(f"    ⚠️  GAP DETECTED: {gap.sequence_gap_size} messages missed!")
                else:
                    print(f"    ✅ No gap (sequence OK)")

            prev_seq = snapshot.sequence_id

            # Stop after 10 snapshots
            if count >= 10:
                break

        # Final health check
        health = await adapter.health_check()
        print(f"\n📊 Final Health:")
        print(f"    Status: {health.status.value}")
        print(f"    Messages: {health.message_count}")
        print(f"    Lag: {health.lag_ms}ms")
        print(f"    Reconnects: {health.reconnect_count}")
        print(f"    Gaps (last hour): {health.gaps_last_hour}")

        # Disconnect
        print("\n🔌 Disconnecting...")
        await adapter.disconnect()
        print("✅ Disconnected cleanly")

        print("\n" + "=" * 80)
        print("🎉 BINANCE TEST PASSED!")
        print("=" * 80)
        return True

    except Exception as e:
        print(f"\n❌ BINANCE TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_okx_connection():
    """Test real OKX WebSocket connection."""
    print("\n" + "=" * 80)
    print("🟠 OKX INTEGRATION TEST")
    print("=" * 80)

    try:
        # Load configuration
        config = load_config()
        exchange_config = config.get_exchange("okx")
        instruments = [config.get_instrument("BTC-USDT-PERP")]

        if not exchange_config or not instruments[0]:
            print("❌ Configuration not found. Check config/exchanges.yaml and config/instruments.yaml")
            return False

        # Create adapter
        adapter = OKXAdapter(exchange_config, instruments)
        print("✅ OKX adapter created")

        # Connect
        print("🔌 Connecting to OKX WebSocket...")
        await adapter.connect()
        print("✅ Connected to OKX!")

        # Check health before subscribing
        health = await adapter.health_check()
        print(f"📊 Initial Health: {health.status.value}, Connected: {adapter.is_connected}")

        # Subscribe
        print("📡 Subscribing to BTC-USDT-PERP...")
        await adapter.subscribe(["BTC-USDT-PERP"])
        print("✅ Subscribed!")

        # Stream data
        print("\n📈 Streaming live order book data (10 snapshots)...")
        print("-" * 80)

        count = 0
        prev_seq = None

        async for snapshot in adapter.stream_order_books():
            count += 1

            # Display snapshot info
            print(f"\n[{count}] {snapshot.instrument} @ {snapshot.timestamp.strftime('%H:%M:%S.%f')[:-3]}")
            print(f"    Best Bid: {snapshot.best_bid:,.2f} USDT")
            print(f"    Best Ask: {snapshot.best_ask:,.2f} USDT")
            print(f"    Spread:   {snapshot.spread_bps:.2f} bps")
            print(f"    Sequence: {snapshot.sequence_id}")

            # Check for gaps
            if prev_seq is not None:
                gap = adapter.detect_gap(prev_seq, snapshot.sequence_id)
                if gap:
                    print(f"    ⚠️  GAP DETECTED: {gap.sequence_gap_size} messages missed!")
                else:
                    print(f"    ✅ No gap (sequence OK)")

            prev_seq = snapshot.sequence_id

            # Stop after 10 snapshots
            if count >= 10:
                break

        # Final health check
        health = await adapter.health_check()
        print(f"\n📊 Final Health:")
        print(f"    Status: {health.status.value}")
        print(f"    Messages: {health.message_count}")
        print(f"    Lag: {health.lag_ms}ms")
        print(f"    Reconnects: {health.reconnect_count}")
        print(f"    Gaps (last hour): {health.gaps_last_hour}")

        # Disconnect
        print("\n🔌 Disconnecting...")
        await adapter.disconnect()
        print("✅ Disconnected cleanly")

        print("\n" + "=" * 80)
        print("🎉 OKX TEST PASSED!")
        print("=" * 80)
        return True

    except Exception as e:
        print(f"\n❌ OKX TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_concurrent_connections():
    """Test running both exchanges concurrently."""
    print("\n" + "=" * 80)
    print("🔵🟠 CONCURRENT CONNECTION TEST")
    print("=" * 80)
    print("Testing both Binance and OKX streaming simultaneously...\n")

    try:
        # Load configuration
        config = load_config()

        # Create adapters
        binance = BinanceAdapter(
            config.get_exchange("binance"),
            [config.get_instrument("BTC-USDT-PERP")]
        )
        okx = OKXAdapter(
            config.get_exchange("okx"),
            [config.get_instrument("BTC-USDT-PERP")]
        )

        # Connect both
        await binance.connect()
        await okx.connect()
        print("✅ Both exchanges connected")

        # Subscribe both
        await binance.subscribe(["BTC-USDT-PERP"])
        await okx.subscribe(["BTC-USDT-PERP"])
        print("✅ Both exchanges subscribed")

        # Stream from both concurrently
        print("\n📈 Streaming from both exchanges (5 snapshots each)...\n")

        async def stream_binance():
            count = 0
            async for snapshot in binance.stream_order_books():
                print(f"🔵 BINANCE: {snapshot.best_bid:,.2f} / {snapshot.best_ask:,.2f} (spread: {snapshot.spread_bps:.2f} bps)")
                count += 1
                if count >= 5:
                    break

        async def stream_okx():
            count = 0
            async for snapshot in okx.stream_order_books():
                print(f"🟠 OKX:     {snapshot.best_bid:,.2f} / {snapshot.best_ask:,.2f} (spread: {snapshot.spread_bps:.2f} bps)")
                count += 1
                if count >= 5:
                    break

        # Run both streams concurrently
        await asyncio.gather(stream_binance(), stream_okx())

        # Disconnect both
        await binance.disconnect()
        await okx.disconnect()

        print("\n" + "=" * 80)
        print("🎉 CONCURRENT TEST PASSED!")
        print("=" * 80)
        return True

    except Exception as e:
        print(f"\n❌ CONCURRENT TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all integration tests."""
    print("\n" + "=" * 80)
    print("🚀 EXCHANGE ADAPTER INTEGRATION TESTS")
    print("=" * 80)
    print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\nThese tests connect to REAL exchange WebSocket endpoints.")
    print("Make sure you have an active internet connection.")
    print("=" * 80)

    results = {}

    # Test Binance
    results['binance'] = await test_binance_connection()
    await asyncio.sleep(2)  # Brief pause between tests

    # Test OKX
    results['okx'] = await test_okx_connection()
    await asyncio.sleep(2)

    # Test concurrent
    results['concurrent'] = await test_concurrent_connections()

    # Summary
    print("\n" + "=" * 80)
    print("📊 TEST SUMMARY")
    print("=" * 80)
    for name, passed in results.items():
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{name.upper()}: {status}")

    all_passed = all(results.values())
    print("\n" + "=" * 80)
    if all_passed:
        print("🎉 ALL TESTS PASSED! Adapters are working with real exchanges.")
    else:
        print("❌ SOME TESTS FAILED. Check the output above for details.")
    print("=" * 80)
    print(f"End Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
