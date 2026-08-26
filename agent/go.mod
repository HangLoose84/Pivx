module github.com/pivx/agent

go 1.26.3

// NOTA (Fase 2): gVisor se obtiene de su rama especial `go`, pensada para
// consumirse como modulo. Ejecuta UNA vez, antes de compilar:
//
//     go get gvisor.dev/gvisor@go
//     go mod tidy
//
// Esos comandos anadiran gvisor.dev/gvisor a este bloque require con la
// pseudo-version correcta. Se deja fuera a proposito para no fijar un hash
// que quede obsoleto.
require (
	github.com/google/uuid v1.6.0
	github.com/gorilla/websocket v1.5.3
)

require gvisor.dev/gvisor v0.0.0-20260826025903-5083fc811497

require (
	github.com/google/btree v1.1.2 // indirect
	golang.org/x/exp v0.0.0-20250711185948-6ae5c78190dc // indirect
	golang.org/x/sys v0.43.0 // indirect
	golang.org/x/time v0.15.0 // indirect
)
