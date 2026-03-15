"""End-to-end smoke test — starts daemon, runs client operations, verifies results.

Tests daemon lifecycle, JSON-RPC protocol, and client SDK.
Ollama-dependent tests are skipped if Ollama is not reachable.
"""

import json
import subprocess
import sys
import time

SOCKET = "/tmp/yaaos-test-modelbus.sock"


def main():
    # Start daemon in background
    print("Starting daemon...")
    daemon = subprocess.Popen(
        [sys.executable, "-m", "yaaos_modelbus.daemon", "--socket", SOCKET],
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    time.sleep(4)  # Wait for daemon to initialize

    if daemon.poll() is not None:
        print(f"Daemon exited early with code {daemon.returncode}")
        print(daemon.stderr.read().decode())
        sys.exit(1)

    passed = 0
    skipped = 0
    failed = 0
    ollama_ok = False

    try:
        from yaaos_modelbus.client import ModelBusClient

        c = ModelBusClient(SOCKET)

        # 1. Health check — always works
        print("\n=== HEALTH ===")
        h = c.health()
        print(json.dumps(h, indent=2))
        assert h.get("status") in ("healthy", "degraded"), f"Bad status: {h.get('status')}"
        assert "providers" in h, "Missing providers in health"
        assert "resources" in h, "Missing resources in health"
        print("PASS: Health check OK")
        passed += 1

        # Check if Ollama is reachable
        ollama_health = h.get("providers", {}).get("ollama", {})
        ollama_ok = ollama_health.get("healthy", False)
        if ollama_ok:
            print("  Ollama is reachable — running full tests")
        else:
            print(f"  Ollama not reachable — skipping Ollama-dependent tests")
            print(f"  (Error: {ollama_health.get('error', 'unknown')})")

        # 2. List models
        print("\n=== MODELS ===")
        models = c.list_models()
        for m in models:
            print(f"  {m['id']} — {m.get('capabilities', [])}")
        if ollama_ok:
            assert len(models) > 0, "No models returned"
            print(f"PASS: {len(models)} models available")
            passed += 1
        else:
            print(f"SKIP: Ollama not reachable ({len(models)} models)")
            skipped += 1

        # 3. Embed
        print("\n=== EMBED ===")
        if ollama_ok:
            r = c.embed(["hello world", "semantic file search"])
            print(f"  Model: {r.get('model')}")
            print(f"  Dims: {r.get('dims')}")
            print(f"  Vectors: {len(r.get('embeddings', []))}")
            e = r["embeddings"][0]
            print(f"  First 5: {[round(x, 4) for x in e[:5]]}")
            assert len(r["embeddings"]) == 2, f"Expected 2 vectors, got {len(r['embeddings'])}"
            assert r["dims"] > 0, "Dims should be > 0"
            print("PASS: Embedding OK")
            passed += 1
        else:
            print("SKIP: No embedding provider available")
            skipped += 1

        # 4. Generate
        print("\n=== GENERATE ===")
        if ollama_ok:
            text = c.generate("What is 2+2? Answer in one word.", max_tokens=20)
            print(f"  Response: {text!r}")
            assert len(text) > 0, "Empty generation"
            print("PASS: Generation OK")
            passed += 1
        else:
            print("SKIP: No generation provider available")
            skipped += 1

        # 5. Protocol test — invalid method should return error
        print("\n=== ERROR HANDLING ===")
        try:
            c._run(c._async._request("nonexistent.method"))
            print("FAIL: Should have raised error for unknown method")
            failed += 1
        except Exception as e:
            print(f"  Got expected error: {e}")
            print("PASS: Error handling OK")
            passed += 1

        # 6. Ping
        print("\n=== PING ===")
        alive = c.ping()
        assert alive, "Ping should return True"
        print("PASS: Ping OK")
        passed += 1

        # Summary
        print(f"\n{'=' * 40}")
        print(f"PASSED: {passed}  SKIPPED: {skipped}  FAILED: {failed}")
        if failed == 0:
            print("=== ALL TESTS PASSED ===")
        else:
            print("=== SOME TESTS FAILED ===")
            sys.exit(1)

    except Exception as e:
        print(f"\nFAILED: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        daemon.terminate()
        daemon.wait(timeout=10)
        print("Daemon stopped.")


if __name__ == "__main__":
    main()
