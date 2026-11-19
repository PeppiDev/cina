import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import List, Optional, Tuple
from PIL import Image


_PNG_IEND = b"IEND\xaeB`\x82"


def _get_fast_temp_dir() -> str:
    """Usa /dev/shm (RAM disk en Linux) cuando está disponible para evitar I/O de disco."""
    shm = "/dev/shm"
    if os.path.isdir(shm) and os.access(shm, os.W_OK):
        return shm
    return tempfile.gettempdir()


class ScreenCapture:
    def __init__(self, preferred_backend: str = "auto"):
        self.preferred_backend = preferred_backend
        self.backend = self._detect_backend()
        print(f"[Capture] Backend de captura seleccionado: {self.backend}")

    def _is_wayland(self) -> bool:
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        return "wayland" in session_type or bool(os.environ.get("WAYLAND_DISPLAY"))

    def _is_niri(self) -> bool:
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        return bool(os.environ.get("NIRI_SOCKET")) or "niri" in desktop

    def _get_niri_focused_output(self) -> Optional[str]:
        """Obtiene el nombre del monitor enfocado en Niri para capturas precisas en multi-monitor."""
        if not self._is_niri() or not shutil.which("niri"):
            return None
        try:
            res = subprocess.run(
                ["niri", "msg", "--json", "focused-output"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=0.5,
            )
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, dict):
                    name = data.get("name")
                    if isinstance(name, str) and name.strip():
                        return name.strip()
        except Exception:
            pass
        return None

    def _has_mss(self) -> bool:
        try:
            import mss  # noqa: F401
            return True
        except Exception:
            return False

    def _is_backend_available(self, name: str) -> bool:
        if name == "pillow":
            try:
                from PIL import ImageGrab  # noqa: F401
                return True
            except Exception:
                return False
        if name == "mss":
            return not self._is_wayland() and self._has_mss()
        if name == "niri":
            return self._is_niri() and shutil.which("niri") is not None
        return shutil.which(name) is not None

    def _detect_backend(self) -> str:
        if self.preferred_backend != "auto":
            if self._is_backend_available(self.preferred_backend):
                return self.preferred_backend

        is_wayland = self._is_wayland()
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

        # 1. En KDE Plasma (Wayland/X11), Spectacle es el backend nativo recomendado
        if "kde" in desktop or "plasma" in desktop:
            if shutil.which("spectacle"):
                return "spectacle"

        # 2. En Wayland con wlr-screencopy (Niri, Sway, Hyprland, etc.), grim es ultra-rápido (~30ms)
        if is_wayland and shutil.which("grim"):
            return "grim"

        # 3. En Niri sin grim, usar la acción nativa de captura por IPC de Niri
        if self._is_backend_available("niri"):
            return "niri"

        # 4. Spectacle genérico si está instalado
        if shutil.which("spectacle"):
            return "spectacle"

        # 5. En X11, mss (XShm en RAM) es el más rápido (~20ms)
        if not is_wayland and self._has_mss():
            return "mss"

        # 6. En GNOME
        if shutil.which("gnome-screenshot"):
            return "gnome-screenshot"

        # 7. En X11 CLI
        if shutil.which("scrot"):
            return "scrot"
        if shutil.which("maim"):
            return "maim"

        # 8. Fallback Python PIL ImageGrab
        if self._is_backend_available("pillow"):
            return "pillow"

        return "unknown"

    def _get_backend_order(self) -> List[str]:
        """Devuelve la lista ordenada de backends a intentar, priorizando siempre self.backend."""
        all_candidates = [
            "grim",
            "niri",
            "spectacle",
            "mss",
            "scrot",
            "maim",
            "gnome-screenshot",
            "pillow",
        ]
        # Respetar primero el backend detectado/elegido por el usuario
        order: List[str] = []
        if self.backend and self.backend != "unknown" and self._is_backend_available(self.backend):
            order.append(self.backend)

        is_wayland = self._is_wayland()
        for b in all_candidates:
            if b in order:
                continue
            # Evitar herramientas exclusivas de Wayland en X11 y viceversa si no aplican
            if b in ("grim", "niri") and not is_wayland:
                continue
            if b in ("mss", "scrot", "maim") and is_wayland:
                continue
            if self._is_backend_available(b):
                order.append(b)
        return order

    def _run_spectacle(self, output_path: str, timeout: float = 4.0) -> bool:
        """Ejecuta spectacle en modo no interactivo (-b -n) con fallback de monitor activo (-m) a pantalla completa."""
        for flags in (["-m", "-b", "-n"], ["-b", "-n"]):
            cmd = ["spectacle", *flags, "-o", output_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    return True
            except Exception as e:
                print(f"[Capture] Falló spectacle ({' '.join(flags)}): {e}")
        return False

    def _capture_via_niri_ipc(self, tmp_dir: str, timeout: float = 2.5) -> Optional[Tuple[bytes, str]]:
        """Captura la pantalla activa usando `niri msg action screenshot-screen` en /dev/shm."""
        tmp_subdir = tempfile.mkdtemp(prefix="cina_niri_", dir=tmp_dir)
        shot_path = os.path.join(tmp_subdir, "shot.png")
        try:
            launched = False
            for cmd in (
                ["niri", "msg", "action", "screenshot-screen", "--path", shot_path, "--show-pointer", "false"],
                ["niri", "msg", "action", "screenshot-screen", "--path", shot_path],
            ):
                try:
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2.0)
                    if res.returncode == 0:
                        launched = True
                        break
                except Exception as e:
                    print(f"[Capture] Falló comando niri IPC: {e}")

            if not launched:
                return None

            # niri msg action screenshot-screen escribe el PNG de forma asíncrona: esperar cierre completo
            deadline = time.monotonic() + timeout
            prev_size = -1
            stable_ticks = 0
            while time.monotonic() < deadline:
                if os.path.exists(shot_path):
                    try:
                        size = os.path.getsize(shot_path)
                    except OSError:
                        size = 0
                    if size > 1000:
                        with open(shot_path, "rb") as f:
                            data = f.read()
                        if data.endswith(_PNG_IEND):
                            return data, "png"
                        if size == prev_size:
                            stable_ticks += 1
                            if stable_ticks >= 2:
                                return data, "png"
                        else:
                            prev_size = size
                            stable_ticks = 0
                time.sleep(0.015)

            if os.path.exists(shot_path) and os.path.getsize(shot_path) > 1000:
                with open(shot_path, "rb") as f:
                    return f.read(), "png"
            return None
        finally:
            shutil.rmtree(tmp_subdir, ignore_errors=True)

    def _capture_raw_bytes_via_backend(self, backend: str, quality: int = 82) -> Optional[Tuple[bytes, str]]:
        """
        Captura la pantalla usando un backend específico y devuelve (bytes, formato_hint).
        Evita compresión PNG lenta siempre que sea posible y usa /dev/shm (RAM).
        """
        tmp_dir = _get_fast_temp_dir()

        # 1. Grim: salida directa JPEG por stdout (cero I/O de disco, ~30-50ms)
        # En Niri multi-monitor prioriza el monitor enfocado con `-o <output>`
        if backend == "grim":
            grim_cmds: List[List[str]] = []
            focused_output = self._get_niri_focused_output()
            if focused_output:
                grim_cmds.append(["grim", "-o", focused_output, "-t", "jpeg", "-q", str(quality), "-"])
            grim_cmds.append(["grim", "-t", "jpeg", "-q", str(quality), "-"])

            for cmd in grim_cmds:
                try:
                    res = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=3,
                    )
                    if res.returncode == 0 and len(res.stdout) > 1000:
                        return res.stdout, "jpeg"
                except Exception as e:
                    print(f"[Capture] Falló grim ({' '.join(cmd[:3])}): {e}")
            return None

        # 1b. Niri IPC nativo (fallback cuando grim no está disponible en Niri)
        if backend == "niri":
            return self._capture_via_niri_ipc(tmp_dir)

        # 2. MSS (X11 XShm directo en RAM, ~15-30ms)
        if backend == "mss":
            try:
                import mss
                with mss.mss() as sct:
                    monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                    raw = sct.grab(monitor)
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=quality)
                    return buf.getvalue(), "jpeg"
            except Exception as e:
                print(f"[Capture] Falló mss: {e}")
            return None

        # 3. Spectacle: escribir JPEG directo en /dev/shm (mucho más rápido que PNG)
        if backend == "spectacle":
            for suffix, fmt in ((".jpg", "jpeg"), (".png", "png")):
                fd, tmp_path = tempfile.mkstemp(prefix="cina_cap_", suffix=suffix, dir=tmp_dir)
                os.close(fd)
                try:
                    if self._run_spectacle(tmp_path):
                        with open(tmp_path, "rb") as f:
                            data = f.read()
                        if len(data) > 1000:
                            return data, fmt
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
            return None

        # 4. Scrot (X11 CLI) -> JPEG en /dev/shm
        if backend == "scrot":
            fd, tmp_path = tempfile.mkstemp(prefix="cina_cap_", suffix=".jpg", dir=tmp_dir)
            os.close(fd)
            try:
                res = subprocess.run(
                    ["scrot", "-z", "-q", str(quality), "-o", tmp_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=3,
                )
                if res.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) <= 1000:
                    # Compatibilidad con versiones antiguas de scrot sin -o
                    res = subprocess.run(
                        ["scrot", "-z", tmp_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=3,
                    )
                if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
                    with open(tmp_path, "rb") as f:
                        return f.read(), "jpeg"
            except Exception as e:
                print(f"[Capture] Falló scrot: {e}")
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
            return None

        # 5. Maim (X11 stdout directo JPEG)
        if backend == "maim":
            try:
                res = subprocess.run(
                    ["maim", "-f", "jpg", "-m", "1"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=3,
                )
                if res.returncode == 0 and len(res.stdout) > 1000:
                    return res.stdout, "jpeg"
            except Exception as e:
                print(f"[Capture] Falló maim: {e}")
            return None

        # 6. GNOME Screenshot
        if backend == "gnome-screenshot":
            fd, tmp_path = tempfile.mkstemp(prefix="cina_cap_", suffix=".png", dir=tmp_dir)
            os.close(fd)
            try:
                res = subprocess.run(
                    ["gnome-screenshot", "-f", tmp_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=4,
                )
                if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
                    with open(tmp_path, "rb") as f:
                        return f.read(), "png"
            except Exception as e:
                print(f"[Capture] Falló gnome-screenshot: {e}")
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
            return None

        # 7. Fallback Python PIL ImageGrab
        if backend == "pillow":
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                return buf.getvalue(), "jpeg"
            except Exception as e:
                print(f"[Capture] Falló ImageGrab: {e}")
            return None

        return None

    def capture_to_jpeg_bytes(
        self,
        max_width: int = 1600,
        quality: int = 82,
        make_thumbnail: bool = False,
        thumb_size: Tuple[int, int] = (260, 200),
    ) -> Optional[Tuple[bytes, Optional[Image.Image]]]:
        """
        Ruta de máxima velocidad (en memoria):
        Captura la pantalla directamente en memoria y devuelve
        (jpeg_bytes, thumbnail_img) evitando escrituras redundantes en disco.
        """
        raw_result = None
        for backend in self._get_backend_order():
            raw_result = self._capture_raw_bytes_via_backend(backend, quality=quality)
            if raw_result:
                # Recordar backend funcional para próximas capturas
                self.backend = backend
                break

        if not raw_result:
            return None

        raw_bytes, fmt = raw_result

        try:
            with Image.open(io.BytesIO(raw_bytes)) as img:
                w, h = img.size
                needs_resize = w > max_width
                needs_convert = img.mode not in ("RGB", "L")
                needs_reencode = (fmt != "jpeg") or needs_resize or needs_convert

                thumb_img: Optional[Image.Image] = None
                if make_thumbnail:
                    thumb_copy = img.copy()
                    thumb_copy.thumbnail(thumb_size, Image.Resampling.BILINEAR)
                    thumb_img = thumb_copy

                if not needs_reencode:
                    # Ya es un JPEG de tamaño óptimo: cero CPU extra
                    return raw_bytes, thumb_img

                work_img = img.convert("RGB") if needs_convert else img
                if needs_resize:
                    ratio = max_width / float(w)
                    new_h = max(1, int(float(h) * ratio))
                    # BICUBIC es ~3x más rápido que LANCZOS manteniendo nitidez en texto
                    work_img = work_img.resize((max_width, new_h), Image.Resampling.BICUBIC)

                out_buf = io.BytesIO()
                work_img.save(out_buf, format="JPEG", quality=quality)
                return out_buf.getvalue(), thumb_img
        except Exception as e:
            print(f"[Capture] Error procesando imagen en memoria: {e}")
            if fmt == "jpeg":
                return raw_bytes, None
            return None

    def capture_to_file(self, output_path: Optional[str] = None) -> Optional[str]:
        """
        Toma una captura de pantalla y la guarda como JPEG en output_path (o /dev/shm).
        """
        created_temp = False
        if not output_path:
            tmp_dir = _get_fast_temp_dir()
            tmp_fd, output_path = tempfile.mkstemp(prefix="cina_raw_", suffix=".jpg", dir=tmp_dir)
            os.close(tmp_fd)
            created_temp = True

        output_path = os.path.abspath(output_path)
        result = self.capture_to_jpeg_bytes(max_width=1920, quality=85, make_thumbnail=False)
        if not result:
            if created_temp and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return None

        jpeg_bytes, _ = result
        try:
            with open(output_path, "wb") as f:
                f.write(jpeg_bytes)
            return output_path
        except Exception as e:
            print(f"[Capture] Error guardando captura en {output_path}: {e}")
            if created_temp and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            return None

    @staticmethod
    def optimize_image(input_path: str, max_width: int = 1600, quality: int = 82) -> str:
        """
        Optimiza y comprime la imagen en disco (compatibilidad con llamadas heredadas).
        """
        try:
            base, _ = os.path.splitext(input_path)
            optimized_path = f"{base}_opt.jpg"
            with Image.open(input_path) as img:
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                w, h = img.size
                if w > max_width:
                    ratio = max_width / float(w)
                    new_h = max(1, int(float(h) * ratio))
                    img = img.resize((max_width, new_h), Image.Resampling.BICUBIC)

                img.save(optimized_path, "JPEG", quality=quality)

            return optimized_path
        except Exception as e:
            print(f"[Capture] Error al optimizar imagen: {e}. Usando imagen original.")
            return input_path
