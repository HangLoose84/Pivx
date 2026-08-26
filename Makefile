# Pivx - build del agente Go (Fase 3)
#
# Compila el agente para varias plataformas objetivo, con binarios estaticos y
# despojados de simbolos/DWARF para minimizar el peso (clave para subirlos a un
# target por una conexion inestable durante un CTF).
#
#   make deps            # UNA vez: obtiene gVisor (rama `go`) y resuelve modulos
#   make                 # compila las 3 plataformas en ./dist
#   make linux-amd64     # una plataforma concreta
#   make clean
#
# Flags:
#   -s  elimina la tabla de simbolos
#   -w  elimina la informacion de depuracion DWARF
#   CGO_ENABLED=0  binario estatico (sin dependencias de libc en el target)

AGENT_DIR := agent
DIST      := dist
LDFLAGS   := -s -w
GOFLAGS   := -trimpath
BIN       := pivx-agent

# Build reproducible y estatico.
GO_BUILD = cd $(AGENT_DIR) && CGO_ENABLED=0 GOOS=$(1) GOARCH=$(2) \
	go build $(GOFLAGS) -ldflags="$(LDFLAGS)" -o ../$(DIST)/$(3) .

.PHONY: all all-upx deps linux-amd64 linux-arm64 windows-amd64 \
       linux-amd64-upx linux-arm64-upx windows-amd64-upx clean

all: linux-amd64 linux-arm64 windows-amd64
	@echo "Binarios en ./$(DIST):"
	@ls -lh $(DIST) 2>/dev/null || true

all-upx: all linux-amd64-upx linux-arm64-upx windows-amd64-upx
	@echo "Binarios (normales + UPX) en ./$(DIST):"
	@ls -lh $(DIST) 2>/dev/null || true

# Obtiene la dependencia gVisor (no fijada en go.mod a proposito) y ordena modulos.
deps:
	cd $(AGENT_DIR) && go get gvisor.dev/gvisor@go && go mod tidy

$(DIST):
	mkdir -p $(DIST)

linux-amd64: | $(DIST)
	$(call GO_BUILD,linux,amd64,$(BIN)-linux-amd64)

linux-arm64: | $(DIST)
	$(call GO_BUILD,linux,arm64,$(BIN)-linux-arm64)

windows-amd64: | $(DIST)
	$(call GO_BUILD,windows,amd64,$(BIN)-windows-amd64.exe)

linux-amd64-upx: linux-amd64
	upx --best $(DIST)/$(BIN)-linux-amd64 -o $(DIST)/$(BIN)-linux-amd64-upx

linux-arm64-upx: linux-arm64
	upx --best $(DIST)/$(BIN)-linux-arm64 -o $(DIST)/$(BIN)-linux-arm64-upx

windows-amd64-upx: windows-amd64
	upx --best $(DIST)/$(BIN)-windows-amd64.exe -o $(DIST)/$(BIN)-windows-amd64-upx.exe

clean:
	rm -rf $(DIST)
