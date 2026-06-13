FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git

WORKDIR /app
COPY . .
RUN go mod tidy
RUN go build -o mcp-tools-service ./cmd/server

FROM alpine:latest
WORKDIR /app
COPY --from=builder /app/mcp-tools-service .
EXPOSE 8080
CMD ["./mcp-tools-service"]