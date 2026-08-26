module github.com/pivx/agent

go 1.22

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
