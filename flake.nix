{
  description = "Cina - Asistente de accesibilidad visual y síntesis de voz de baja latencia para Linux";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;

      # Piper TTS sólo para inferencia (onnxruntime): sin torch/lightning/librosa de entrenamiento.
      # Se usa en proceso como módulo Python para mantener el modelo de voz cargado en memoria.
      mkPiperLite = pkgs:
        let
          args = pkgs.piper-tts.override.__functionArgs or { };
        in
        if (pkgs ? piper-tts) && (args ? withTrain) then
          pkgs.piper-tts.override {
            withTrain = false;
            withHTTP = false;
            withJapanese = false;
            withAlignment = false;
          }
        else
          null;

      mkPythonEnv = pkgs:
        let
          lib = pkgs.lib;
          piperLite = mkPiperLite pkgs;
        in
        pkgs.python3.withPackages (ps:
          [
            ps.pillow
            ps.tkinter
            ps.requests
          ]
          ++ lib.optional (ps ? google-genai) ps.google-genai
          ++ lib.optional (ps ? edge-tts) ps.edge-tts
          ++ lib.optional (ps ? pynput) ps.pynput
          ++ lib.optional (ps ? mss) ps.mss
          ++ lib.optional (piperLite != null) (ps.toPythonModule piperLite)
        );
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          lib = pkgs.lib;

          pythonEnv = mkPythonEnv pkgs;

          runtimeTools = [
            pkgs.pipewire
            pkgs.socat
            pkgs.espeak-ng
            pkgs.grim
            pkgs.scrot
          ];

          cinaPkg = pkgs.stdenvNoCC.mkDerivation {
            pname = "cina";
            version = "1.1.0";
            src = self;

            nativeBuildInputs = [ pkgs.makeWrapper ];

            dontBuild = true;

            installPhase = ''
              runHook preInstall

              mkdir -p $out/share/cina $out/bin $out/share/applications
              cp -r main.py cina cina-trigger $out/share/cina/
              chmod +x $out/share/cina/main.py $out/share/cina/cina-trigger

              makeWrapper ${pythonEnv}/bin/python3 $out/bin/cina \
                --add-flags "$out/share/cina/main.py" \
                --suffix PATH : "${lib.makeBinPath runtimeTools}"

              makeWrapper $out/share/cina/cina-trigger $out/bin/cina-trigger \
                --prefix PATH : "${lib.makeBinPath [ pkgs.socat pythonEnv ]}" \
                --suffix PATH : "${lib.makeBinPath runtimeTools}"

              cat > $out/share/applications/cina.desktop <<EOF
              [Desktop Entry]
              Name=Cina Voice Reader
              Comment=Asistente de accesibilidad visual y lectura por voz en tiempo real
              Exec=$out/bin/cina
              Icon=audio-volume-high
              Type=Application
              Terminal=false
              Categories=Utility;Accessibility;
              EOF

              cat > $out/share/applications/cina-trigger.desktop <<EOF
              [Desktop Entry]
              Name=Cina Screen Reader Trigger
              Comment=Disparador rápido de lectura visual por voz Cina
              Exec=$out/bin/cina-trigger
              Icon=audio-volume-high
              Type=Application
              Terminal=false
              NoDisplay=false
              Categories=Utility;Accessibility;
              X-KDE-Shortcuts=Ctrl+Alt+S
              EOF

              runHook postInstall
            '';

            meta = with lib; {
              description = "Asistente de accesibilidad visual y lectura por voz de baja latencia para Linux";
              platforms = platforms.linux;
              mainProgram = "cina";
            };
          };
        in
        {
          default = cinaPkg;
          cina = cinaPkg;
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/cina";
        };
        trigger = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/cina-trigger";
        };
      });

      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          lib = pkgs.lib;

          pythonEnv = mkPythonEnv pkgs;
        in
        {
          default = pkgs.mkShell {
            packages = [
              pythonEnv
              pkgs.pipewire
              pkgs.socat
              pkgs.espeak-ng
              pkgs.grim
              pkgs.scrot
            ];

            shellHook = ''
              export CINA_USE_SYSTEM_PYTHON=1
              echo "[Cina NixOS] Entorno listo. Ejecuta: ./run.sh (GUI), ./run.sh --headless o python3 -m unittest discover -s tests -v"
            '';
          };
        }
      );
    };
}
