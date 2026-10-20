from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
PYTHON = Path(sys.executable)
BASE = "http://127.0.0.1:5175"
TEMP_NAME = "__KORA_FULL_DIAGNOSTIC_V4__.txt"
UPLOAD_NAME = "__KORA_UPLOAD_DIAGNOSTIC_V4__.txt"
TEMP = ROOT / "workspace" / TEMP_NAME

PASS = WARN = FAIL = 0
SERVER_PROC: subprocess.Popen | None = None
SERVER_STARTED_BY_TEST = False


def banner(title: str):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72, flush=True)


def ok(msg):
    global PASS
    PASS += 1
    print(f"[OK] {msg}", flush=True)


def warn(msg):
    global WARN
    WARN += 1
    print(f"[WARN] {msg}", flush=True)


def fail(msg):
    global FAIL
    FAIL += 1
    print(f"[FAIL] {msg}", flush=True)


def request(method, path, data=None, files=None, timeout=8):
    try:
        if files:
            boundary = "----KORADIAGBOUNDARYV4"
            body = bytearray()
            for name, filename, content in files:
                body.extend(f"--{boundary}\r\n".encode())
                body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
                body.extend(b"Content-Type: text/plain\r\n\r\n")
                body.extend(content)
                body.extend(b"\r\n")
            body.extend(f"--{boundary}--\r\n".encode())
            payload = bytes(body)
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        else:
            payload = None if data is None else json.dumps(data).encode()
            headers = {} if payload is None else {"Content-Type": "application/json"}
        req = urllib.request.Request(BASE + path, data=payload, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return r.status, parsed
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return exc.code, parsed
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def api_reachable(timeout=1.5):
    code, body = request("GET", "/api/health", timeout=timeout)
    return code == 200, body


def start_server_if_needed():
    global SERVER_PROC, SERVER_STARTED_BY_TEST
    alive, _ = api_reachable()
    if alive:
        ok("KORA API server already reachable on port 5175")
        return True

    print("[INFO] KORA API server is not running; starting the actual main.py server for API tests...", flush=True)
    try:
        env = dict(os.environ)
        env["KORA_NO_GUI"] = "1"  # headless: no window/browser tab during automated diagnostics
        SERVER_PROC = subprocess.Popen(
            [str(PYTHON), str(ROOT / "main.py")],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        SERVER_STARTED_BY_TEST = True
    except Exception as exc:
        fail(f"Could not start KORA API server: {type(exc).__name__}: {exc}")
        return False

    deadline = time.time() + 30
    while time.time() < deadline:
        alive, body = api_reachable(timeout=1.5)
        if alive:
            ok("KORA API server started and /api/health is reachable")
            return True
        if SERVER_PROC.poll() is not None:
            fail(f"KORA API server exited during startup with code {SERVER_PROC.returncode}")
            return False
        time.sleep(0.5)
    fail("KORA API server did not become reachable within 30 seconds")
    return False


def stop_server_if_started():
    global SERVER_PROC
    if not SERVER_STARTED_BY_TEST or SERVER_PROC is None:
        return
    print("[INFO] Stopping KORA API server started by this diagnostic...", flush=True)
    try:
        SERVER_PROC.terminate()
        SERVER_PROC.wait(timeout=5)
    except Exception:
        try:
            SERVER_PROC.kill()
        except Exception:
            pass


def main():
    global FAIL
    print("=" * 72)
    print("KORA V13 - FULL SYSTEM / API DIAGNOSTIC V5")
    print("=" * 72)
    print(f"Project: {ROOT}")
    print(f"Python:  {PYTHON}")

    banner("1. PROJECT STRUCTURE")
    required = [
        "config.py", "main.py", "api/server.py", "core/engine.py", "core/agent.py",
        "ai/llm.py", "ingestion/manager.py", "ingestion/extractor.py", "knowledge/store.py",
        "retrieval/search.py", "conversation/manager.py", "tools/registry.py", "tools/builtins.py",
        "runtime_manager/model_runtime.py", "media/store.py", "exporter/chat_export.py",
        "web/index.html", "web/app.js", "web/media.js", "web/style.css",
    ]
    for rel in required:
        (ok if (ROOT / rel).exists() else fail)(rel)
    for d in ["data", "workspace", "models", "runtime"]:
        (ok if (ROOT / d).is_dir() else fail)(f"Directory exists: {d}/")

    banner("2. IMPORTS / ACTUAL FUNCTION CONTRACTS")
    try:
        sys.path.insert(0, str(ROOT))
        from config import DATA_DIR, WORKSPACE_DIR, INDEX_PATH, CHAT_URL, EMBED_URL, API_PORT, CHAT_MODEL, EMBED_MODEL
        from ingestion.extractor import extract_file, normalize_records
        from core.engine import KoraEngine
        from knowledge.store import KnowledgeStore
        from retrieval.search import SearchEngine
        from ai.llm import LLMClient
        from tools.registry import ToolRegistry
        ok("Core modules imported")
        print(f"[INFO] normalize_records signature: {inspect.signature(normalize_records)}")
        print(f"[INFO] LLMClient.chat signature: {inspect.signature(LLMClient.chat)}")
        if "max_tokens" in str(inspect.signature(LLMClient.chat)):
            ok("LLMClient.chat accepts max_tokens")
        else:
            ok("LLMClient.chat contract verified; diagnostic will call it without unsupported max_tokens")
    except Exception as exc:
        fail(f"Core imports failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    banner("3. PATHS / MODELS / RUNTIME")
    for p in [DATA_DIR, WORKSPACE_DIR, ROOT / "models", ROOT / "runtime"]:
        (ok if p.exists() else fail)(f"Path accessible: {p}")
    for m in [CHAT_MODEL, EMBED_MODEL]:
        (ok if (ROOT / "models" / m).exists() else fail)(f"Model present: {m}")
    (ok if (ROOT / "runtime" / "llama-server.exe").exists() else fail)("llama-server found")
    try:
        from config import find_mmproj
        mm = find_mmproj()
        if mm:
            ok(f"Multimodal projector present (image + voice enabled): {mm.name}")
        else:
            warn("No mmproj*.gguf in models/ -- KORA runs text-only (image + voice input disabled)")
    except Exception as exc:
        warn(f"mmproj check skipped: {type(exc).__name__}: {exc}")
    for mod, why in [("reportlab", "PDF chat export"), ("PIL", "image attachments + export images")]:
        try:
            __import__(mod)
            ok(f"Python package '{mod}' available ({why})")
        except Exception:
            fail(f"Python package '{mod}' is missing ({why}) -- re-run setup_kora.bat")
    try:
        from exporter import chat_export
        pdf = chat_export.to_pdf([{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "# Hi\n- one\n- two\n\n**bold** text", "sources": [{"source": "a.pdf", "page": 2}]}])
        (ok if pdf[:5] == b"%PDF-" else fail)(f"PDF export renders a valid PDF ({len(pdf):,} bytes)")
    except Exception as exc:
        fail(f"PDF export self-test failed: {type(exc).__name__}: {exc}")
    print(f"[INFO] Chat URL: {CHAT_URL} | Embed URL: {EMBED_URL} | API: http://127.0.0.1:{API_PORT}")

    banner("4. LOCAL MODEL SERVICES")
    try:
        from runtime_manager.model_runtime import ModelRuntime
        rt = ModelRuntime()
        rt.ensure_running()
        ok("KORA runtime ensure_running() completed")
    except Exception as exc:
        warn(f"Runtime ensure_running() returned: {type(exc).__name__}: {exc}")
    for label, url in [("Chat", CHAT_URL), ("Embedding", EMBED_URL)]:
        try:
            with urllib.request.urlopen(url + "/v1/models", timeout=10) as r:
                (ok if r.status == 200 else fail)(f"{label} service /v1/models -> HTTP {r.status}")
        except Exception as exc:
            fail(f"{label} service unreachable: {exc}")

    banner("5. LLM CLIENT")
    try:
        q = LLMClient(timeout=30)
        emb = q.embed(["KORA V4 embedding diagnostic"])
        (ok if emb and emb[0] else fail)(f"Embedding endpoint works; vector length={len(emb[0]) if emb else 0}")
        if emb and emb[0] and len(emb[0]) != 768 and "embeddinggemma" in EMBED_MODEL.lower():
            warn(f"EmbeddingGemma normally returns 768 dimensions, got {len(emb[0])} -- check the embedding model/pooling settings")
        qv = q.embed(["a query"], kind="query")
        (ok if qv and qv[0] else fail)("Query-mode embedding (task prefix) works")
        # IMPORTANT: this matches the real V13 LLMClient.chat contract.
        chat = q.chat([{"role": "user", "content": "Reply with exactly OK"}])
        text = json.dumps(chat, ensure_ascii=False)
        (ok if chat else fail)("LLMClient.chat() returned a response")
        if "OK" in text.upper():
            ok("LLMClient.chat() response contains OK")
        else:
            warn(f"LLMClient.chat() responded but did not contain literal OK: {text[:300]}")
    except Exception as exc:
        fail(f"LLM client test failed: {type(exc).__name__}: {exc}")

    banner("6. REAL EXTRACTION + INGESTION")
    TEMP.parent.mkdir(parents=True, exist_ok=True)
    TEMP.write_text("KORA FULL DIAGNOSTIC V4\nDiagnostic revenue value: 405000\nCompany: DiagnosticCo\n", encoding="utf-8")
    print(f"[INFO] Created temporary document: {TEMP}")
    try:
        raw = extract_file(TEMP)
        records = normalize_records(raw)
        ok(f"Extraction + normalization succeeded: raw={len(raw)}, normalized={len(records)}")
        if any("405000" in str(r.get("content", "")) for r in records):
            ok("Diagnostic content survived extraction/normalization")
        else:
            fail("Diagnostic content missing after normalization")
    except Exception as exc:
        fail(f"Extraction/normalization failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    banner("7. REAL ENGINE SYNC + INDEX")
    engine = None
    try:
        engine = KoraEngine()
        result = engine.sync()
        print(f"[INFO] sync() -> {result}")
        (ok if result.get("failed", 0) == 0 else fail)("KoraEngine.sync() completed with zero failed files")
        idx = KnowledgeStore().data
        matches = [r for r in idx.get("records", []) if r.get("source") == TEMP_NAME or str(r.get("path", "")).endswith(TEMP_NAME)]
        (ok if matches else fail)(f"Diagnostic record persisted ({len(matches)} matching record(s))")
        (ok if matches and matches[0].get("embedding") else fail)(f"Persisted embedding length={len(matches[0]['embedding']) if matches and matches[0].get('embedding') else 0}")
    except Exception as exc:
        fail(f"Engine/index test failed: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    banner("8. RETRIEVAL + TOOLS")
    try:
        store = KnowledgeStore()
        search = SearchEngine(store, LLMClient())
        hits = search.search("What is the diagnostic revenue value 405000?", top_k=5)
        (ok if hits else fail)("SearchEngine retrieved diagnostic data")
        from tools.builtins import register_builtins
        reg = ToolRegistry()
        register_builtins(reg, store, search, engine.conversations if engine else None)
        ok("Built-in tools registered")
    except Exception as exc:
        fail(f"Retrieval/tools test failed: {type(exc).__name__}: {exc}")

    banner("9. FLASK API CONTRACTS")
    api_ok = start_server_if_needed()
    if api_ok:
        safe = [
            ("GET", "/"), ("GET", "/index.html"), ("GET", "/api/health"), ("GET", "/api/activity"),
            ("GET", "/api/documents"), ("GET", "/api/library"), ("GET", "/api/progress"), ("GET", "/api/overview"),
            ("GET", "/api/profile"), ("GET", "/api/audit"), ("GET", "/api/pins"), ("GET", "/api/setup"),
            ("GET", "/api/settings"), ("GET", "/api/ui-settings"), ("GET", "/api/sources"), ("GET", "/api/gaps"),
            ("GET", "/app.js"), ("GET", "/media.js"), ("GET", "/style.css"), ("GET", "/api/capabilities"),
        ]
        for method, path in safe:
            print(f"[TEST] {method} {path}", flush=True)
            code, body = request(method, path)
            (ok if code == 200 else fail)(f"{method} {path} -> HTTP {code}")
        posts = [("/api/profile", {}), ("/api/ui-settings", {}), ("/api/settings", {}), ("/api/pins", {})]
        for path, payload in posts:
            print(f"[TEST] POST {path}", flush=True)
            code, _ = request("POST", path, payload)
            (ok if code == 200 else fail)(f"POST {path} -> HTTP {code}")
        for method, path in [("DELETE", "/api/pins"), ("DELETE", "/api/audit")]:
            print(f"[TEST] {method} {path}", flush=True)
            code, _ = request(method, path)
            (ok if code == 200 else fail)(f"{method} {path} -> HTTP {code}")
        for path in ["/api/ai/start", "/api/ai/stop"]:
            print(f"[TEST] POST {path}", flush=True)
            code, _ = request("POST", path)
            (ok if code == 405 else fail)(f"POST {path} -> HTTP {code} (expected: manual control unsupported)")
        code, _ = request("GET", "/api/documents/__missing_v4__.txt")
        (ok if code == 404 else fail)(f"GET missing document -> HTTP {code}")

        banner("10. UPLOAD -> REINDEX -> SOURCE -> PREVIEW")
        upload_content = b"Uploaded diagnostic value: 918273. Company: UploadCo."
        print("[TEST] POST /api/sources (multipart upload)", flush=True)
        code, _ = request("POST", "/api/sources", files=[("file", UPLOAD_NAME, upload_content)])
        (ok if code == 200 else fail)(f"POST /api/sources upload -> HTTP {code}")
        print("[TEST] POST /api/reindex", flush=True)
        code, _ = request("POST", "/api/reindex")
        (ok if code == 200 else fail)(f"POST /api/reindex -> HTTP {code}")
        print("[TEST] GET /api/sources", flush=True)
        code, body = request("GET", "/api/sources")
        found = code == 200 and isinstance(body, dict) and any(s.get("source") == UPLOAD_NAME for s in body.get("sources", []))
        (ok if found else fail)("Uploaded document visible through /api/sources")
        print("[TEST] GET uploaded document", flush=True)
        code, _ = request("GET", "/api/documents/" + UPLOAD_NAME)
        (ok if code == 200 else fail)(f"GET uploaded document -> HTTP {code}")
        print("[TEST] GET uploaded preview", flush=True)
        code, _ = request("GET", "/api/preview/" + UPLOAD_NAME)
        (ok if code == 200 else fail)(f"GET uploaded preview -> HTTP {code}")
        p = WORKSPACE_DIR / UPLOAD_NAME
        if p.exists():
            p.unlink()
        print("[TEST] POST /api/reindex after deleting uploaded file", flush=True)
        code, _ = request("POST", "/api/reindex")
        (ok if code == 200 else fail)(f"Reindex after deleting uploaded file -> HTTP {code}")

        banner("11. REAL CHAT API")
        print("[TEST] POST /api/chat", flush=True)
        code, body = request("POST", "/api/chat", {"message": "What is the diagnostic revenue value in the KORA diagnostic document?", "session_id": ""}, timeout=120)
        if code == 200 and isinstance(body, dict) and body.get("answer"):
            answer = str(body["answer"])
            ok("POST /api/chat returned an answer")
            normalized = "".join(ch for ch in answer if ch.isdigit())
            (ok if "405000" in normalized else warn)(f"Chat answer expected 405000; answer={answer[:300]}")
        else:
            fail(f"/api/chat failed: HTTP {code} {body}")

    chat_session = body.get("session_id") if api_ok and code == 200 and isinstance(body, dict) else None

    if api_ok:
        banner("11b. IMAGE + VOICE + EXPORT")
        import io, struct, math, wave
        _, caps = request("GET", "/api/capabilities")
        mm_on = isinstance(caps, dict) and caps.get("vision")
        if not mm_on:
            warn("Multimodal projector not loaded -- skipping image/voice checks")
        else:
            try:
                from PIL import Image
                buf = io.BytesIO()
                Image.new("RGB", (256, 256), (220, 20, 20)).save(buf, "PNG")
                png = buf.getvalue()
                def multipart(filename, content, ctype):
                    b = "----KORAMM"
                    body_ = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
                    return body_, {"Content-Type": f"multipart/form-data; boundary={b}"}
                def post_file(path, filename, content, ctype):
                    data_, hdr = multipart(filename, content, ctype)
                    try:
                        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data_, headers=hdr, method="POST"), timeout=120) as r:
                            return r.status, json.loads(r.read().decode())
                    except urllib.error.HTTPError as exc:
                        return exc.code, exc.read().decode(errors="replace")
                code_i, info = post_file("/api/media", "diag_red.png", png, "image/png")
                (ok if code_i == 200 and info.get("id") else fail)(f"POST /api/media (image) -> HTTP {code_i}")
                if code_i == 200:
                    c2, ans = request("POST", "/api/chat", {"message": "What single colour fills this image? Answer with one word.", "session_id": "", "attachments": [{"id": info["id"], "name": "diag_red.png"}]}, timeout=180)
                    text_ = str(ans.get("answer", "")) if isinstance(ans, dict) else str(ans)
                    (ok if c2 == 200 and text_ else fail)(f"Chat with an image attachment -> HTTP {c2}")
                    (ok if "red" in text_.lower() else warn)(f"Vision answer expected 'red'; got: {text_[:120]}")
                # a short 440 Hz tone, 16 kHz mono WAV -- checks the audio path end to end
                wb = io.BytesIO()
                with wave.open(wb, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / 16000))) for n in range(16000 * 2)))
                code_a, res = post_file("/api/transcribe", "diag_tone.wav", wb.getvalue(), "audio/wav")
                (ok if code_a == 200 else fail)(f"POST /api/transcribe (audio path) -> HTTP {code_a}" + ("" if code_a == 200 else f" {str(res)[:200]}"))
            except Exception as exc:
                fail(f"Image/voice test failed: {type(exc).__name__}: {exc}")
        if chat_session:
            for fmt in ("pdf", "md"):
                try:
                    with urllib.request.urlopen(f"{BASE}/api/library/{chat_session}/export.{fmt}", timeout=60) as r:
                        raw_ = r.read()
                    good = raw_[:5] == b"%PDF-" if fmt == "pdf" else len(raw_) > 20
                    (ok if good else fail)(f"Export chat as {fmt.upper()} -> {len(raw_):,} bytes")
                except Exception as exc:
                    fail(f"Export chat as {fmt.upper()} failed: {type(exc).__name__}: {exc}")
        else:
            warn("No chat session from step 11 -- skipping export endpoint test")

    banner("12. DELETION RECONCILIATION")
    try:
        if TEMP.exists():
            TEMP.unlink()
        result = KoraEngine().sync()
        print(f"[INFO] cleanup sync() -> {result}")
        stale = [d for d in KnowledgeStore().document_list() if d.get("source") == TEMP_NAME or str(d.get("path", "")).endswith(TEMP_NAME)]
        (ok if not stale else fail)("Deleted diagnostic file was removed from knowledge_index.json")
        stale2 = [d for d in KnowledgeStore().document_list() if d.get("source") == UPLOAD_NAME]
        (ok if not stale2 else fail)("Uploaded diagnostic cleanup is clean")
    except Exception as exc:
        fail(f"Deletion reconciliation test failed: {type(exc).__name__}: {exc}")

    banner("FINAL RESULT")
    print(f"PASSED : {PASS}\nWARNINGS: {WARN}\nFAILED : {FAIL}")
    if FAIL:
        print("\n[FAILURE] One or more real checks failed. Review the first [FAIL] above.")
        return 1
    if WARN:
        print("\n[PASS WITH WARNINGS] Core system checks passed; review warnings above.")
    else:
        print("\n[SUCCESS] KORA V13 full-system diagnostic passed.")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        code = 130
    except Exception as exc:
        traceback.print_exc()
        code = 1
    finally:
        stop_server_if_started()
    raise SystemExit(code)
