import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types as pytypes
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from cina.capture import ScreenCapture
from cina.config import ConfigManager
from cina.gemini_service import GeminiService
from cina.ipc_service import IPCClient, IPCServer
from cina.tts_service import SentenceChunker, TTSService


def _make_sample_image_bytes(width: int = 800, height: int = 600, fmt: str = "JPEG", mode: str = "RGB") -> bytes:
    img = Image.new(mode, (width, height), color=(40, 80, 120) if mode == "RGB" else (40, 80, 120, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


class TestGeminiService(unittest.TestCase):
    def test_deprecated_model_and_thinking_level_safety(self):
        svc = GeminiService(api_key="k1", model="gemini-2.5-flash", thinking_level="minimal")
        self.assertEqual(svc.model, "gemini-3.8-flash")
        self.assertEqual(svc._resolve_thinking_level("gemini-3.8-flash"), "LOW")
        self.assertEqual(svc._resolve_thinking_level("gemini-3.7-flash"), "LOW")
        self.assertEqual(svc._resolve_thinking_level("gemini-3.5-flash-lite"), "MINIMAL")

    def test_candidate_plan_rotates_keys_before_downgrading_model(self):
        svc = GeminiService(api_key="k1, k2", model="gemini-3.8-flash")
        plan, _ = svc._build_candidate_plan()
        self.assertEqual(
            plan,
            [
                (0, "gemini-3.8-flash"),
                (1, "gemini-3.8-flash"),
                (0, "gemini-3.5-flash-lite"),
                (1, "gemini-3.5-flash-lite"),
                (0, "gemini-3.7-flash"),
                (1, "gemini-3.7-flash"),
            ],
        )

    def test_circuit_breaker_skips_cooled_down_and_invalid_keys(self):
        svc = GeminiService(api_key="k1, k2", model="gemini-3.8-flash")
        svc._record_failure(0, "gemini-3.8-flash", "rate_limit", 45.0)
        svc._record_failure(1, "gemini-3.8-flash", "auth", 3600.0)
        plan, _ = svc._build_candidate_plan()
        self.assertEqual(plan, [(0, "gemini-3.5-flash-lite"), (0, "gemini-3.7-flash")])

    def test_chunker_shortens_long_first_sentence(self):
        chunker = SentenceChunker()
        text = ("Estás viendo el perfil de GitHub del usuario PeppiDev, que muestra doscientas "
                "contribuciones en el último año. En la sección de actividad aparecen siete repositorios. ")
        out = chunker.feed(text)
        self.assertTrue(out[0].endswith("PeppiDev,"), out)
        self.assertLessEqual(len(out[0]), 71)
        self.assertEqual(" ".join(out + [chunker.flush() or ""]).split(), text.split())

    def test_chunker_cuts_first_sentence_at_connector_without_commas(self):
        chunker = SentenceChunker()
        out = chunker.feed("La pantalla muestra el panel de configuración del sistema operativo con varias opciones de red y almacenamiento disponibles. ")
        self.assertEqual(out[0], "La pantalla muestra el panel de configuración del sistema operativo")
        self.assertTrue(out[1].startswith("con varias"))

    def test_custom_prompts_are_sent_and_updatable(self):
        from cina.gemini_service import SYSTEM_PROMPT, USER_PROMPT

        svc = GeminiService(api_key="k1", system_prompt="Sistema A", user_prompt="Usuario A")
        self.assertEqual(svc._build_sdk_config(pytypes.SimpleNamespace(GenerateContentConfig=dict), "gemini-3.8-flash", include_advanced=False)["system_instruction"], "Sistema A")
        svc.set_prompts("Sistema B", "Usuario B")
        self.assertEqual((svc.system_prompt, svc.user_prompt), ("Sistema B", "Usuario B"))
        svc.set_prompts("  ", "")
        self.assertEqual((svc.system_prompt, svc.user_prompt), (SYSTEM_PROMPT, USER_PROMPT))

    def test_hedged_request_uses_first_model_that_streams(self):
        svc = GeminiService(api_key="k1", model="gemini-3.8-flash", hedge_delay=0.1, slow_cooldown=60.0)
        calls = []

        def fake_stream_once(key_idx, model, img_bytes, mime_type, on_chunk, cancel_event, t0):
            calls.append(model)
            if model == "gemini-3.8-flash":
                # Modelo saturado: no entrega el primer token hasta que lo cancelan
                for _ in range(100):
                    if cancel_event.is_set():
                        return "", 0.0
                    time.sleep(0.01)
                on_chunk("tarde. ")
                return "tarde.", 1.0
            on_chunk("Respuesta rápida. ")
            on_chunk("Fin.")
            return "Respuesta rápida. Fin.", 0.15

        svc._stream_once = fake_stream_once
        received = []
        result = svc.analyze_image_stream(image_bytes=b"jpeg", on_chunk=received.append)
        self.assertTrue(result.success)
        self.assertEqual(result.model_used, "gemini-3.5-flash-lite")
        self.assertEqual("".join(received), "Respuesta rápida. Fin.")
        self.assertEqual(calls[:2], ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
        # El modelo lento queda en cooldown para el siguiente disparo
        plan, _ = svc._build_candidate_plan()
        self.assertEqual(plan[0], (0, "gemini-3.5-flash-lite"))

    def test_hedged_request_failover_on_fast_error(self):
        svc = GeminiService(api_key="k1", model="gemini-3.8-flash", hedge_delay=5.0)

        def fake_stream_once(key_idx, model, img_bytes, mime_type, on_chunk, cancel_event, t0):
            if model == "gemini-3.8-flash":
                raise RuntimeError("503 UNAVAILABLE high demand")
            on_chunk("Hola.")
            return "Hola.", 0.1

        svc._stream_once = fake_stream_once
        t = time.perf_counter()
        result = svc.analyze_image_stream(image_bytes=b"jpeg")
        self.assertLess(time.perf_counter() - t, 1.0)  # no espera el hedge_delay tras un fallo rápido
        self.assertTrue(result.success)
        self.assertEqual(result.model_used, "gemini-3.5-flash-lite")

    def test_streaming_sdk_failover_and_client_reuse(self):
        created_clients = []
        calls = []

        class FakePart:
            @staticmethod
            def from_bytes(data, mime_type):
                return {"bytes_len": len(data), "mime_type": mime_type}

        class FakeHttpRetryOptions:
            def __init__(self, attempts=1):
                self.attempts = attempts

        class FakeHttpOptions:
            def __init__(self, timeout=None, retry_options=None):
                self.timeout = timeout
                self.retry_options = retry_options

        class FakeThinkingConfig:
            def __init__(self, thinking_level=None):
                self.thinking_level = thinking_level

        class FakeGenerateContentConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeChunk:
            def __init__(self, text):
                self.text = text

        class FakeModels:
            def __init__(self, api_key):
                self.api_key = api_key

            def generate_content_stream(self, model, contents, config):
                calls.append((self.api_key, model, contents[0], config.kwargs))
                if self.api_key == "key1" and model == "gemini-3.8-flash":
                    raise RuntimeError("429 RESOURCE_EXHAUSTED retry in 25.4s")
                return iter([FakeChunk("Resumen breve. "), FakeChunk("Se muestra un editor de texto abierto.")])

        class FakeClient:
            def __init__(self, api_key, http_options=None):
                self.api_key = api_key
                self.http_options = http_options
                self.models = FakeModels(api_key)
                created_clients.append(self)

        fake_types = pytypes.SimpleNamespace(
            Part=FakePart,
            HttpRetryOptions=FakeHttpRetryOptions,
            HttpOptions=FakeHttpOptions,
            ThinkingConfig=FakeThinkingConfig,
            GenerateContentConfig=FakeGenerateContentConfig,
        )
        fake_genai = pytypes.SimpleNamespace(Client=FakeClient, types=fake_types)
        fake_google = pytypes.SimpleNamespace(genai=fake_genai)

        img_bytes = _make_sample_image_bytes(320, 240)
        received = []

        with mock.patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai, "google.genai.types": fake_types}):
            svc = GeminiService(api_key="key1, key2", model="gemini-3.8-flash", thinking_level="low", timeout=12.0)

            res1 = svc.analyze_image_stream(image_bytes=img_bytes, on_chunk=received.append)
            self.assertTrue(res1.success)
            self.assertEqual(res1.text, "Resumen breve. Se muestra un editor de texto abierto.")
            self.assertEqual(res1.model_used, "gemini-3.8-flash")
            self.assertEqual(res1.key_index, 1)
            self.assertEqual(received, ["Resumen breve. ", "Se muestra un editor de texto abierto."])
            self.assertEqual(created_clients[0].http_options.retry_options.attempts, 1)
            self.assertEqual(calls[0][2]["mime_type"], "image/jpeg")

            calls.clear()
            res2 = svc.analyze_image_stream(image_bytes=img_bytes)
            self.assertTrue(res2.success)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "key2")
            self.assertEqual(len(created_clients), 2)

    def test_rest_sse_streaming_fallback_skips_thoughts(self):
        svc = GeminiService(api_key="k1", model="gemini-3.8-flash")
        sse_lines = [
            'data: {"candidates":[{"content":{"parts":[{"text":"procesando...","thought":true}]}}]}',
            'data: {"candidates":[{"content":{"parts":[{"text":"Resultado directo. "}]}}]}',
            'data: {"candidates":[{"content":{"parts":[{"text":"El valor calculado es 5."}]}}]}',
        ]

        class FakeResp:
            status_code = 200
            text = ""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def iter_lines(self, decode_unicode=True):
                return iter(sse_lines)

        fake_session = mock.MagicMock()
        fake_session.post.return_value = FakeResp()
        svc._rest_session = fake_session

        chunks = []
        text, ttft = svc._stream_via_rest(
            api_key="k1",
            model="gemini-3.8-flash",
            img_bytes=b"\xff\xd8\xff\xd9",
            mime_type="image/jpeg",
            on_chunk=chunks.append,
            t0=time.perf_counter(),
        )
        self.assertEqual(chunks, ["Resultado directo. ", "El valor calculado es 5."])
        self.assertEqual(text, "Resultado directo. El valor calculado es 5.")
        self.assertGreaterEqual(ttft, 0.0)

    def test_mid_stream_disconnect_preserves_partial_text(self):
        svc = GeminiService(api_key="k1", model="gemini-3.8-flash")

        def broken_sdk(api_key, model, img_bytes, mime_type, on_chunk=None, cancel_event=None, t0=0.0):
            if on_chunk:
                on_chunk("Resumen inicial. ")
                on_chunk("Contenido visible en pantalla.")
            raise RuntimeError("Connection reset by peer")

        with mock.patch.object(svc, "_stream_via_sdk", side_effect=broken_sdk):
            res = svc.analyze_image_stream(image_bytes=b"\xff\xd8\xff\xd9")
            self.assertTrue(res.success)
            self.assertEqual(res.text, "Resumen inicial. Contenido visible en pantalla.")

    def test_400_thinking_error_retries_without_advanced_config(self):
        configs_seen = []

        class FakePart:
            @staticmethod
            def from_bytes(data, mime_type):
                return (data, mime_type)

        class FakeThinkingConfig:
            def __init__(self, thinking_level=None):
                self.thinking_level = thinking_level

        class FakeGenerateContentConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class FakeChunk:
            def __init__(self, text):
                self.text = text

        class FakeModels:
            def generate_content_stream(self, model, contents, config):
                configs_seen.append(config.kwargs)
                if "thinking_config" in config.kwargs:
                    raise RuntimeError("400 INVALID_ARGUMENT: thinking_level not supported")
                return iter([FakeChunk("Lectura completada.")])

        class FakeClient:
            def __init__(self, api_key, http_options=None):
                self.models = FakeModels()

        fake_types = pytypes.SimpleNamespace(
            Part=FakePart,
            ThinkingConfig=FakeThinkingConfig,
            GenerateContentConfig=FakeGenerateContentConfig,
        )
        fake_genai = pytypes.SimpleNamespace(Client=FakeClient, types=fake_types)
        fake_google = pytypes.SimpleNamespace(genai=fake_genai)

        with mock.patch.dict(sys.modules, {"google": fake_google, "google.genai": fake_genai, "google.genai.types": fake_types}):
            svc = GeminiService(api_key="k1", model="gemini-3.8-flash", thinking_level="low")
            res = svc.analyze_image_stream(image_bytes=b"\xff\xd8\xff\xd9")
            self.assertTrue(res.success)
            self.assertEqual(res.text, "Lectura completada.")
            self.assertEqual(len(configs_seen), 2)
            self.assertIn("thinking_config", configs_seen[0])
            self.assertNotIn("thinking_config", configs_seen[1])


class TestTTSService(unittest.TestCase):
    def test_sentence_chunker_fast_first_sentence_and_decimals(self):
        chunker = SentenceChunker()
        out1 = chunker.feed("Elemento 2. El valor es 3.")
        self.assertEqual(out1, ["Elemento 2."])
        out2 = chunker.feed("14 y corresponde a la constante matemática pi en el documento. Segunda frase.")
        self.assertEqual(out2, ["El valor es 3.14 y corresponde a la constante matemática pi en el documento."])
        self.assertEqual(chunker.flush(), "Segunda frase.")

    def test_clean_text_preserves_list_numbers_and_strips_markdown(self):
        tts = TTSService()
        raw = "**1. Primer paso.** Usa `print()` en [Python](https://python.org).\n- 2. Segundo paso."
        cleaned = tts.clean_text_for_speech(raw)
        self.assertIn("1. Primer paso.", cleaned)
        self.assertIn("2. Segundo paso.", cleaned)
        self.assertIn("print()", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("`", cleaned)
        self.assertNotIn("https://", cleaned)

    def test_streaming_session_cancellation_prevents_overlapping_audio(self):
        tts = TTSService(engine="edge-tts")
        played = []
        gate = threading.Event()

        def fake_synth(sentence, voice, cancel_event):
            if "primera" in sentence:
                gate.wait(timeout=2.0)
            fd, p = tempfile.mkstemp(prefix="test_tts_", suffix=".mp3")
            os.close(fd)
            with open(p, "wb") as f:
                f.write(b"ID3" + b"\x00" * 200)
            return p

        def fake_play(path, cancel_event=None):
            if cancel_event and cancel_event.is_set():
                return
            played.append(path)

        tts._synthesize_sentence = fake_synth
        tts._play_audio_file = fake_play

        s1 = tts.start_stream()
        s1.push("Esta es la primera lectura lenta.")
        s1.finish()
        time.sleep(0.05)

        s2 = tts.start_stream()
        s2.push("Lectura nueva prioritaria.")
        s2.finish()
        gate.set()

        self.assertTrue(s1.wait(timeout=3.0))
        self.assertTrue(s2.wait(timeout=3.0))
        self.assertEqual(len(played), 1)
        self.assertFalse(os.path.exists(played[0]))


class TestScreenCapture(unittest.TestCase):
    def test_preferred_backend_is_tried_first(self):
        with mock.patch.object(ScreenCapture, "_is_backend_available", side_effect=lambda b: b in ("spectacle", "grim", "pillow")), \
             mock.patch.object(ScreenCapture, "_is_wayland", return_value=True):
            cap = ScreenCapture(preferred_backend="grim")
            self.assertEqual(cap.backend, "grim")
            order = cap._get_backend_order()
            self.assertEqual(order[0], "grim")

    def test_capture_to_jpeg_bytes_resizes_and_creates_thumbnail(self):
        raw_png = _make_sample_image_bytes(2560, 1440, fmt="PNG", mode="RGBA")
        cap = ScreenCapture(preferred_backend="pillow")
        with mock.patch.object(cap, "_get_backend_order", return_value=["pillow"]), \
             mock.patch.object(cap, "_capture_raw_bytes_via_backend", return_value=(raw_png, "png")):
            res = cap.capture_to_jpeg_bytes(max_width=1600, quality=80, make_thumbnail=True)
            self.assertIsNotNone(res)
            jpeg_bytes, thumb = res
            self.assertIsNotNone(thumb)
            self.assertLessEqual(thumb.width, 260)
            with Image.open(io.BytesIO(jpeg_bytes)) as out_img:
                self.assertEqual(out_img.format, "JPEG")
                self.assertEqual(out_img.width, 1600)
                self.assertEqual(out_img.height, 900)

    def test_niri_grim_targets_focused_output_first(self):
        sample_jpeg = _make_sample_image_bytes(1280, 720, fmt="JPEG", mode="RGB")
        commands_run = []

        def fake_run(cmd, **kwargs):
            commands_run.append(cmd)
            if cmd[:3] == ["niri", "msg", "--json"]:
                return subprocess.CompletedProcess(cmd, 0, stdout='{"name": "DP-1"}', stderr="")
            if cmd[:3] == ["grim", "-o", "DP-1"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=sample_jpeg, stderr=b"")
            return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"err")

        env = {"NIRI_SOCKET": "/run/user/1000/niri.sock", "WAYLAND_DISPLAY": "wayland-1", "XDG_SESSION_TYPE": "wayland"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("shutil.which", side_effect=lambda b: f"/usr/bin/{b}" if b in ("grim", "niri") else None), \
             mock.patch("subprocess.run", side_effect=fake_run):
            cap = ScreenCapture(preferred_backend="auto")
            self.assertEqual(cap.backend, "grim")
            res = cap._capture_raw_bytes_via_backend("grim", quality=82)
            self.assertIsNotNone(res)
            data, fmt = res
            self.assertEqual(fmt, "jpeg")
            self.assertEqual(data, sample_jpeg)
            self.assertIn(["grim", "-o", "DP-1", "-t", "jpeg", "-q", "82", "-"], commands_run)

    def test_niri_native_ipc_backend_waits_for_async_png_and_cleans_up(self):
        sample_png = _make_sample_image_bytes(640, 480, fmt="PNG", mode="RGB")
        created_paths = []

        def fake_run(cmd, **kwargs):
            if cmd[:4] == ["niri", "msg", "action", "screenshot-screen"]:
                shot_path = cmd[cmd.index("--path") + 1]
                created_paths.append(shot_path)

                def delayed_write():
                    time.sleep(0.03)
                    with open(shot_path, "wb") as f:
                        f.write(sample_png)

                threading.Thread(target=delayed_write, daemon=True).start()
                return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"")

        env = {"NIRI_SOCKET": "/run/user/1000/niri.sock", "WAYLAND_DISPLAY": "wayland-1", "XDG_SESSION_TYPE": "wayland"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("shutil.which", side_effect=lambda b: "/usr/bin/niri" if b == "niri" else None), \
             mock.patch("subprocess.run", side_effect=fake_run):
            cap = ScreenCapture(preferred_backend="auto")
            self.assertEqual(cap.backend, "niri")
            res = cap.capture_to_jpeg_bytes(max_width=1600, quality=82)
            self.assertIsNotNone(res)
            jpeg_bytes, _ = res
            self.assertGreater(len(jpeg_bytes), 500)
            self.assertEqual(len(created_paths), 1)
            self.assertFalse(os.path.exists(os.path.dirname(created_paths[0])))

    def test_hotkey_service_skips_pynput_on_pure_wayland_niri(self):
        from cina.hotkey_service import HotkeyService

        clean_env = {"WAYLAND_DISPLAY": "wayland-1", "XDG_SESSION_TYPE": "wayland", "NIRI_SOCKET": "/tmp/niri.sock"}
        with mock.patch.dict(os.environ, clean_env, clear=True):
            hs = HotkeyService("<ctrl>+<alt>+s", on_trigger=lambda: None)
            self.assertTrue(hs._is_pure_wayland_without_x11())
            hs.start()
            try:
                self.assertIsNone(hs._listener)
                self.assertTrue(IPCClient.is_server_running())
            finally:
                hs.stop()


class TestIPCAndFastTrigger(unittest.TestCase):
    def test_ipc_trigger_stop_and_shell_fast_path(self):
        triggered = threading.Event()
        stopped = threading.Event()

        server = IPCServer(on_trigger=triggered.set, on_stop=stopped.set)
        server.start()
        try:
            self.assertTrue(IPCClient.is_server_running())

            t0 = time.perf_counter()
            res = subprocess.run([os.path.join(REPO_ROOT, "cina-trigger")], capture_output=True, text=True, timeout=3)
            dt = time.perf_counter() - t0
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertTrue(triggered.wait(timeout=1.0))
            self.assertLess(dt, 0.5)

            res_stop = subprocess.run([os.path.join(REPO_ROOT, "cina-trigger"), "--stop"], capture_output=True, text=True, timeout=3)
            self.assertEqual(res_stop.returncode, 0, msg=res_stop.stderr)
            self.assertTrue(stopped.wait(timeout=1.0))
        finally:
            server.stop()


class TestConfigAtomicSave(unittest.TestCase):
    def test_atomic_save_permissions_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_dir = Path(tmpdir)
            tmp_file = tmp_dir / "config.json"
            with mock.patch("cina.config.CONFIG_DIR", tmp_dir), \
                 mock.patch("cina.config.CONFIG_FILE", tmp_file):
                cm = ConfigManager()
                cm.set("model", "gemini-3.8-flash")
                self.assertTrue(tmp_file.exists())
                mode = stat.S_IMODE(os.stat(tmp_file).st_mode)
                self.assertEqual(mode, 0o600)
                with open(tmp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["model"], "gemini-3.8-flash")
                self.assertEqual(list(tmp_dir.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
