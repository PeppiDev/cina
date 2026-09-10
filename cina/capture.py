import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image

class ScreenCapture:
    def __init__(self, preferred_backend: str = "auto"):
        self.preferred_backend = preferred_backend
        self.backend = self._detect_backend()
        print(f"[Capture] Backend de captura seleccionado: {self.backend}")

    def _detect_backend(self) -> str:
        if self.preferred_backend != "auto":
            if shutil.which(self.preferred_backend):
                return self.preferred_backend

        # 1. En entornos KDE Plasma (Fedora/Nobara/Kubuntu/Arch con KDE), Spectacle es el estándar
        if shutil.which("spectacle"):
            return "spectacle"

        # 2. En Wayland genérico (Sway, Hyprland, etc.)
        session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if "wayland" in session_type:
            if shutil.which("grim"):
                return "grim"

        # 3. En GNOME
        if shutil.which("gnome-screenshot"):
            return "gnome-screenshot"

        # 4. En X11
        if shutil.which("scrot"):
            return "scrot"
        if shutil.which("maim"):
            return "maim"

        # 5. Fallback a Python PIL ImageGrab / mss
        try:
            from PIL import ImageGrab
            return "pillow"
        except Exception:
            pass

        return "unknown"

    def capture_silent(self, output_path: Optional[str] = None) -> Optional[str]:
        """
        Toma una captura de pantalla completa de forma 100% silenciosa:
        - Sin sonido de obturador
        - Sin ventana emergente
        - Sin notificación en el escritorio
        - Sin robar foco
        """
        if not output_path:
            tmp_fd, output_path = tempfile.mkstemp(prefix="cina_raw_", suffix=".png")
            os.close(tmp_fd)

        output_path = os.path.abspath(output_path)
        success = False

        # 1. KDE Spectacle (Completamente silencioso con -m -b -n para capturar la pantalla actual)
        if self.backend == "spectacle" or shutil.which("spectacle"):
            cmd = ["spectacle", "-m", "-b", "-n", "-o", output_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    success = True
                else:
                    # Fallback a capturar todas las pantallas si -m falla
                    cmd = ["spectacle", "-b", "-n", "-o", output_path]
                    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
                    if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                        success = True
            except Exception as e:
                print(f"[Capture] Falló spectacle: {e}")

        # 2. Grim (Wayland nativo silencioso)
        if not success and (self.backend == "grim" or shutil.which("grim")):
            cmd = ["grim", output_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    success = True
            except Exception as e:
                print(f"[Capture] Falló grim: {e}")

        # 3. Scrot (X11 con flag -z silencioso)
        if not success and (self.backend == "scrot" or shutil.which("scrot")):
            cmd = ["scrot", "-z", output_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    success = True
            except Exception as e:
                print(f"[Capture] Falló scrot: {e}")

        # 4. GNOME Screenshot
        if not success and (self.backend == "gnome-screenshot" or shutil.which("gnome-screenshot")):
            cmd = ["gnome-screenshot", "-f", output_path]
            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4)
                if res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    success = True
            except Exception as e:
                print(f"[Capture] Falló gnome-screenshot: {e}")

        # 5. Fallback Python PIL ImageGrab
        if not success:
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
                img.save(output_path, "PNG")
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    success = True
            except Exception as e:
                print(f"[Capture] Falló ImageGrab: {e}")

        if success:
            return output_path
        return None

    @staticmethod
    def optimize_image(input_path: str, max_width: int = 1920, quality: int = 85) -> str:
        """
        Optimiza y comprime la imagen para acelerar el envío a Gemini (reduce latencia).
        Mantiene la nitidez para la lectura de texto pero reduce el tamaño a ~200-400KB.
        """
        try:
            optimized_path = input_path.replace(".png", "_opt.jpg")
            with Image.open(input_path) as img:
                # Convertir a RGB si tiene canal Alpha
                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                # Redimensionar si excede el tamaño máximo manteniendo proporción
                w, h = img.size
                if w > max_width:
                    ratio = max_width / float(w)
                    new_h = int(float(h) * ratio)
                    img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)

                img.save(optimized_path, "JPEG", quality=quality, optimize=True)

            return optimized_path
        except Exception as e:
            print(f"[Capture] Error al optimizar imagen: {e}. Usando imagen original.")
            return input_path

