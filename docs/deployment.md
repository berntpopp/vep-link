# Deployment

`vep-link` is a stateless live-API client: there is no data bundle, no ingest
step and no volume to provision. The container serves traffic as soon as it
boots.

## Docker

The Compose stack publishes the internal container port `8000` on host port
**8021** (override with `VEP_LINK_HOST_PORT`), bound to loopback:

```bash
docker compose -f docker/docker-compose.yml up
curl http://localhost:8021/health
```

When running under Docker, use `http://localhost:8021/mcp` everywhere a local
non-Docker server would use `http://127.0.0.1:8000/mcp`.

Two overlays sit on top of the base stack:

| Overlay | Purpose |
|---------|---------|
| [`docker/docker-compose.prod.yml`](../docker/docker-compose.prod.yml) | Production: no published ports (`ports: !reset []`), container hardening. |
| [`docker/docker-compose.npm.yml`](../docker/docker-compose.npm.yml) | Deployment behind Nginx Proxy Manager on a shared proxy network. |

In production the container is **not** published directly. It is fronted by a
reverse proxy that terminates TLS, and the proxy's hostname must be added as an
exact entry to `VEP_LINK_MCP_ALLOWED_HOSTS` (see
[configuration](configuration.md#host-origin-and-cors)) or every proxied request
is rejected by the Host guard.

## MCP client configuration

### HTTP

```bash
# hosted
claude mcp add --transport http vep-link https://vep-link.genefoundry.org/mcp
# local dev server
claude mcp add --transport http vep-link http://127.0.0.1:8000/mcp
# Docker stack
claude mcp add --transport http vep-link http://localhost:8021/mcp
```

```json
{
  "mcpServers": {
    "vep-link": {
      "type": "http",
      "url": "http://localhost:8021/mcp"
    }
  }
}
```

### stdio (local entrypoint)

`mcp_server.py` runs the same MCP facade over stdio. There is **no dedicated
console script** for stdio — `vep-link serve` only supports the `unified` and
`http` transports — so the module must be invoked directly:

```json
{
  "mcpServers": {
    "vep-link": {
      "command": "uv",
      "args": ["run", "python", "mcp_server.py"],
      "cwd": "/path/to/vep-link"
    }
  }
}
```

## Behind the GeneFoundry router

Like every `-link` backend, `vep-link` is **unauthenticated by design**: the
[genefoundry-router](https://github.com/berntpopp/genefoundry-router) owns edge
auth at the trust boundary. The backend MUST therefore be reachable only through
the router or a reverse proxy, never published directly to the internet.

The router mounts this server under the namespace token `vep`, so
`annotate_variant` is surfaced to hosts as `vep_annotate_variant`.

## Production notes

- Prefer Streamable HTTP MCP behind HTTPS; protect public deployments with an
  authenticated reverse proxy.
- Set `VEP_LINK_LOG_FORMAT=json` (the default) so logs are machine-parseable.
- Keep MCP tools research-use scoped; never imply clinical decision support.
- Treat live Ensembl rate limits as upstream state, not a local failure. The
  per-assembly circuit breaker will report a degraded upstream through
  `check_upstream_health`, the `vep://health` resource, and `_meta.upstream` on
  every response.
