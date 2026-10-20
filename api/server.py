from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import time
import sys
from flask import Flask, jsonify, request, send_from_directory, Response

from config import find_mmproj, AUDIO_CHUNK_SECONDS, API_HOST, API_PORT, WEB_DIR, WORKSPACE_DIR, CHAT_MODEL, EMBED_MODEL, CHAT_URL, EMBED_URL, SETTINGS_PATH
from core.engine import KoraEngine
from media import store as media_store
from exporter import chat_export
from runtime_manager.model_runtime import ModelRuntime, RuntimeErrorKora


class KoraServer:
    def __init__(self) -> None:
        self.app = Flask(__name__, static_folder=None)
        self.engine = KoraEngine()
        self.runtime = ModelRuntime()
        self.boot_error: str | None = None
        self._routes()

    @staticmethod
    def _clean_attachments(raw) -> list[dict]:
        """Keep only attachments that really exist in the media store."""
        out = []
        for item in (raw or [])[:6]:
            info = media_store.describe(str((item or {}).get("id", "")))
            if info:
                out.append({"id": info["id"], "kind": info["kind"], "name": str(item.get("name") or info["kind"])[:120]})
        return out

    @staticmethod
    def _title(messages) -> str:
        first = next((m for m in messages if m.get("role") == "user"), None)
        if not first:
            return "Conversation"
        if first.get("content"):
            return first["content"]
        kinds = {a.get("kind") for a in first.get("attachments") or []}
        return "Voice message" if kinds == {"audio"} else "Image" if kinds == {"image"} else "Attachment"

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Sanitize an uploaded filename for safe on-disk storage.

        This mirrors werkzeug's secure_filename() (ASCII-fold, drop path
        separators and unsafe characters) but does NOT strip leading/
        trailing '_' characters the way secure_filename()'s final
        .strip('._') does. That stripping is meant to guard against
        dotfile/traversal tricks ('.', '..', '.hidden') but it also
        silently mangles ordinary filenames that start or end with
        underscores (e.g. "__report__.txt" -> "report__.txt"), which
        breaks any client that expects the returned/indexed source name
        to match what it uploaded. We only strip leading dots (the
        actual traversal/hidden-file risk) and leave underscores intact.
        """
        import re
        import unicodedata

        name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
        name = name.replace("/", " ").replace("\\", " ")
        name = re.sub(r"[^A-Za-z0-9_.\- ]", "", name)
        name = "_".join(name.split())
        name = name.lstrip(".")
        return name

    def _routes(self) -> None:
        app = self.app

        @app.get("/")
        def index():
            return send_from_directory(WEB_DIR, "index.html")

        @app.get("/<path:filename>")
        def static_file(filename: str):
            return send_from_directory(WEB_DIR, filename)

        @app.get("/api/health")
        def health():
            chat_ok = self.runtime._ready(CHAT_URL)
            embed_ok = self.runtime._ready(EMBED_URL)
            indexing_active = bool(self.engine.progress.get("active"))
            ready = chat_ok and embed_ok

            if not ready:
                step, percent, label = "ai", 55, "Starting the local AI"
                message = "Waiting for the local models to come online…"
            elif indexing_active:
                step, percent, label = "index", 85, "Indexing your files"
                message = self.engine.progress.get("message") or "Indexing your knowledge base…"
            else:
                step, percent, label = "ready", 100, "Ready"
                message = "KORA is ready."

            boot = {
                "ready": ready,
                "step": step,
                "percent": percent,
                "cap": min(99, percent + 8),
                "label": label,
                "message": message,
            }
            payload = {"ok": True, "engine": "python-agent", "chat_runtime": chat_ok, "embedding_runtime": embed_ok, "llm_online": chat_ok, "ready": ready, "starting": not ready, "ai_starting": not ready, "boot": boot, "model": CHAT_MODEL, "embedding_model": EMBED_MODEL, "multimodal": bool(find_mmproj())}
            if not ready and self.boot_error:
                payload["error"] = self.boot_error
            return jsonify(payload)

        @app.get("/api/capabilities")
        def capabilities():
            mm = bool(find_mmproj())
            return jsonify({"vision": mm, "audio": mm, "audio_chunk_seconds": AUDIO_CHUNK_SECONDS})

        @app.post("/api/media")
        def media_upload():
            file = request.files.get("file")
            if not file or not file.filename:
                return jsonify({"error": "No file supplied"}), 400
            if not find_mmproj():
                return jsonify({"error": "Image/audio input needs the Gemma multimodal projector (mmproj*.gguf) in the models folder."}), 400
            try:
                info = media_store.save_upload(file.filename, file.mimetype or "", file.read())
            except media_store.MediaError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({**info, "url": f"/api/media/{info['id']}"})

        @app.get("/api/media/<media_id>")
        def media_get(media_id: str):
            path = media_store.path_for(media_id)
            if not path:
                return jsonify({"error": "Not found"}), 404
            return send_from_directory(path.parent, path.name, max_age=86400)

        @app.post("/api/transcribe")
        def transcribe():
            """Speech -> text for voice commands. Accepts one wav/mp3 clip (<= ~30 s)."""
            file = request.files.get("file")
            if not file:
                return jsonify({"error": "No audio supplied"}), 400
            if not find_mmproj():
                return jsonify({"error": "Voice input needs the Gemma multimodal projector (mmproj*.gguf) in the models folder."}), 400
            raw = file.read()
            if not raw:
                return jsonify({"error": "Empty recording"}), 400
            ext = ".mp3" if (file.filename or "").lower().endswith(".mp3") else ".wav"
            try:
                text = self.engine.llm.transcribe(media_store.audio_part(raw, ext), str(request.form.get("language", "")))
                return jsonify({"text": text})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.post("/api/chat")
        def chat():
            body = request.get_json(silent=True) or {}
            try:
                result = self.engine.chat(str(body.get("session_id") or ""), str(body.get("message", body.get("question", ""))).strip(), self._clean_attachments(body.get("attachments")), bool(body.get("voice")))
                return jsonify(result)
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.post("/api/chat/stream")
        def chat_stream():
            # Streaming needs the request body read up-front: it's parsed on
            # this (request) thread, but the generator below runs lazily as
            # the WSGI server pulls chunks to write to the socket, by which
            # point Flask's request context may already be gone.
            body = request.get_json(silent=True) or {}
            session_id = str(body.get("session_id") or "")
            message = str(body.get("message", body.get("question", ""))).strip()
            attachments = self._clean_attachments(body.get("attachments"))
            voice = bool(body.get("voice"))

            def generate():
                try:
                    for event in self.engine.chat_stream(session_id, message, attachments, voice):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except Exception as exc:
                    yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

            return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        @app.get("/api/activity")
        def activity():
            return jsonify(self.engine.progress)

        @app.post("/api/reindex")
        def reindex():
            try:
                return jsonify(self.engine.sync())
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.get("/api/documents")
        def documents():
            docs = self.engine.store.document_list()
            return jsonify({"documents": docs, "duplicates": []})

        @app.get("/api/library")
        def library():
            sessions = []
            for item in self.engine.conversations.list():
                messages = item.get("messages", [])
                title = self._title(messages)
                sessions.append({"id": item["id"], "title": title[:80], "messages": messages})
            return jsonify({"sessions": sessions})

        @app.get("/api/library/<session_id>")
        def library_get(session_id: str):
            messages = self.engine.conversations.get(session_id)
            title = self._title(messages)
            return jsonify({"id": session_id, "title": title[:80], "messages": messages})

        @app.get("/api/library/<session_id>/export.<fmt>")
        def library_export(session_id: str, fmt: str):
            messages = self.engine.conversations.sessions.get(session_id)
            if not messages:
                return jsonify({"error": "Conversation not found or empty"}), 404
            title = chat_export.conversation_title(messages)
            try:
                if fmt == "pdf":
                    data, mime = chat_export.to_pdf(messages), "application/pdf"
                elif fmt in ("md", "markdown"):
                    data, mime, fmt = chat_export.to_markdown(messages).encode("utf-8"), "text/markdown; charset=utf-8", "md"
                else:
                    return jsonify({"error": "Unsupported format"}), 400
            except ImportError as exc:
                return jsonify({"error": f"PDF export needs the 'reportlab' package ({exc}). Run setup_kora.bat again."}), 500
            except Exception as exc:
                return jsonify({"error": f"Export failed: {exc}"}), 500
            name = chat_export.safe_filename(title, fmt)
            # HTTP headers must be latin-1: a Hindi/emoji title in the filename would
            # otherwise crash the response with "Internal Server Error". Send an
            # ASCII-safe name plus the full UTF-8 name via RFC 5987 filename*.
            import unicodedata
            from urllib.parse import quote
            ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or f"KORA_conversation.{fmt}"
            ascii_name = ascii_name.replace('"', "")
            return Response(data, mimetype=mime, headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
                "X-Export-Filename": ascii_name,
            })

        @app.delete("/api/library/<session_id>")
        def library_delete(session_id: str):
            self.engine.conversations.sessions.pop(session_id, None)
            self.engine.conversations.save()
            return jsonify({"ok": True})

        @app.post("/api/sources")
        def upload():
            file = request.files.get("file")
            if not file or not file.filename:
                return jsonify({"error": "No file supplied"}), 400

            safe = self._sanitize_filename(file.filename)
            if not safe:
                return jsonify({"error": "Invalid filename"}), 400

            # Optional company/workspace folder.  If omitted, keep the file
            # at the workspace root so existing clients remain compatible.
            company = str(request.form.get("company", "")).strip()
            if company:
                from werkzeug.utils import secure_filename

                company = secure_filename(company)
                if not company:
                    return jsonify({"error": "Invalid company name"}), 400
                target_dir = (WORKSPACE_DIR / company).resolve()
            else:
                target_dir = WORKSPACE_DIR.resolve()

            if WORKSPACE_DIR.resolve() not in target_dir.parents and target_dir != WORKSPACE_DIR.resolve():
                return jsonify({"error": "Invalid destination"}), 400

            target_dir.mkdir(parents=True, exist_ok=True)
            target = (target_dir / safe).resolve()
            if WORKSPACE_DIR.resolve() not in target.parents:
                return jsonify({"error": "Invalid destination"}), 400

            file.save(target)

            # Upload is an ingestion operation, not merely a filesystem copy.
            # Sync immediately so the same KoraEngine instance updates the
            # knowledge store before the API reports success.
            try:
                result = self.engine.sync()
            except Exception as exc:
                try:
                    target.unlink(missing_ok=True)
                except Exception:
                    pass
                return jsonify({"ok": False, "filename": safe, "error": str(exc)}), 500

            resolved = str(target.resolve())
            document = next(
                (doc for doc in self.engine.store.document_list()
                 if str(doc.get("path", "")) == resolved),
                None,
            )

            if document is None:
                return jsonify({
                    "ok": False,
                    "filename": safe,
                    "status": "not_indexed",
                    "sync": result,
                    "error": "File was saved but KORA did not register it in the knowledge index.",
                }), 500

            return jsonify({
                "ok": True,
                "filename": safe,
                "company": company or None,
                "status": "indexed",
                "document": document,
                "sync": result,
            })

        @app.get("/api/progress")
        def progress():
            return jsonify(self.engine.progress)

        @app.get("/api/overview")
        def overview():
            docs = self.engine.store.document_list()
            return jsonify({"documents": len(docs), "indexed_documents": len(docs), "records": len(self.engine.store.all_records()), "model": CHAT_MODEL, "embedding_model": EMBED_MODEL})

        @app.get("/api/profile")
        def profile_get():
            return jsonify({"profile": {"name": "", "role": "user"}})

        @app.post("/api/profile")
        def profile_post():
            return jsonify({"profile": request.get_json(silent=True) or {}})

        @app.get("/api/audit")
        def audit():
            return jsonify({"events": []})

        @app.delete("/api/audit")
        def clear_audit():
            return jsonify({"ok": True})

        @app.get("/api/pins")
        def pins_get():
            return jsonify({"pins": []})

        @app.post("/api/pins")
        def pins_post():
            return jsonify({"pins": []})

        @app.delete("/api/pins")
        def pins_delete():
            return jsonify({"pins": []})

        @app.get("/api/setup")
        def setup():
            return jsonify({"ready": self.runtime._ready(CHAT_URL) and self.runtime._ready(EMBED_URL), "models": [CHAT_MODEL], "recommended_model": CHAT_MODEL, "settings": {"model": CHAT_MODEL}})

        @app.get("/api/settings")
        def settings_get():
            data = {"model": CHAT_MODEL, "embedding_model": EMBED_MODEL}
            if SETTINGS_PATH.exists():
                try:
                    data.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
                except Exception:
                    pass
            return jsonify(data)

        @app.post("/api/settings")
        def settings_post():
            body = request.get_json(silent=True) or {}
            body["model"] = CHAT_MODEL
            body["embedding_model"] = EMBED_MODEL
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            return jsonify({"ok": True, **body})

        @app.get("/api/system")
        def system_info():
            import autotune

            return jsonify(autotune.describe(self.runtime.active))

        @app.post("/api/system/performance")
        def system_performance():
            import autotune

            body = request.get_json(silent=True) or {}
            prefs = autotune.save_prefs(str(body.get("profile", "auto")), bool(body.get("force_gpu", False)))
            return jsonify({"ok": True, "prefs": prefs, "restart_required": True})

        @app.post("/api/open-logs")
        def open_logs():
            from config import LOG_DIR

            try:
                if os.name == "nt":
                    os.startfile(LOG_DIR)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(LOG_DIR)])
                else:
                    subprocess.Popen(["xdg-open", str(LOG_DIR)])
                return jsonify({"ok": True})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.get("/api/ui-settings")
        def ui_settings_get():
            return jsonify({"settings": {}})

        @app.post("/api/ui-settings")
        def ui_settings_post():
            return jsonify({"settings": request.get_json(silent=True) or {}})

        @app.post("/api/clear")
        def clear_chat():
            return jsonify({"ok": True})

        @app.post("/api/open-workspace")
        def open_workspace():
            try:
                if os.name == "nt":
                    os.startfile(WORKSPACE_DIR)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(WORKSPACE_DIR)])
                else:
                    subprocess.Popen(["xdg-open", str(WORKSPACE_DIR)])
                return jsonify({"ok": True})
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        @app.get("/api/documents/<path:name>")
        def document_detail(name: str):
            for doc in self.engine.store.document_list():
                if doc.get("source") == name or doc.get("path") == name:
                    return jsonify(doc)
            return jsonify({"error": "Document not found"}), 404

        @app.get("/api/source-context/<record_id>")
        def source_context(record_id: str):
            for record in self.engine.store.all_records():
                if record.get("record_id") == record_id:
                    return jsonify({"source": record.get("source"), "location": record.get("path"), "content": record.get("content", "")})
            return jsonify({"error": "Source not found"}), 404

        @app.get("/api/preview/<path:name>")
        def preview(name: str):
            for record in self.engine.store.all_records():
                if record.get("source") == name:
                    return jsonify({"text": record.get("content", ""), "truncated": False})
            return jsonify({"error": "Document not found"}), 404

        @app.delete("/api/sources/<path:name>")
        def delete_source(name: str):
            for doc in self.engine.store.document_list():
                if doc.get("source") == name or doc.get("path") == name:
                    document_id = doc["document_id"]
                    physical_path = Path(str(doc.get("path", ""))).resolve()
                    self.engine.store.remove_document(document_id)
                    # Keep the filesystem and knowledge index consistent.
                    # Never allow deletion outside workspace.
                    workspace_root = WORKSPACE_DIR.resolve()
                    if workspace_root in physical_path.parents and physical_path.exists():
                        try:
                            physical_path.unlink()
                        except OSError as exc:
                            return jsonify({
                                "ok": False,
                                "error": f"Index entry removed but physical file could not be deleted: {exc}",
                            }), 500
                    return jsonify({"ok": True, "document_id": document_id})
            return jsonify({"error": "Document not found"}), 404

        @app.get("/api/sources")
        def sources():
            return jsonify({"sources": self.engine.store.document_list()})

        @app.post("/api/ai/start")
        def ai_start():
            return jsonify({"ok": False, "error": "AI runtime is managed automatically by KORA; manual start/stop is not supported."}), 405

        @app.post("/api/ai/stop")
        def ai_stop():
            return jsonify({"ok": False, "error": "AI runtime is managed automatically by KORA; manual start/stop is not supported."}), 405

        @app.get("/api/gaps")
        def gaps():
            return jsonify({"gaps": []})

    def _start_models(self) -> None:
        """Start the llama.cpp services in the background so the HTTP server (and
        therefore the window) is available immediately; /api/health reports
        progress to the boot screen."""
        import logging
        import threading

        log = logging.getLogger("kora.server")
        try:
            self.runtime.ensure_running()
            threading.Thread(target=self.engine.sync, daemon=True, name="kora-sync").start()
        except RuntimeErrorKora as exc:
            log.error("Runtime not ready: %s", exc)
            if exc.fatal:
                # Missing file / crashed process: will never resolve on its own,
                # so surface it to the UI instead of an endless boot screen.
                self.boot_error = str(exc)
            else:
                log.warning("Model processes are still starting in the background; the UI updates once they respond.")
        except Exception as exc:  # noqa: BLE001
            log.exception("Unexpected failure starting the model runtime")
            self.boot_error = f"Unexpected error starting the AI: {exc}"

    def run(self) -> None:
        import threading

        threading.Thread(target=self._start_models, daemon=True, name="kora-models").start()
        self.app.run(host=API_HOST, port=API_PORT, threaded=True)
