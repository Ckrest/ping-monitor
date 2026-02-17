# Ping Monitor

Lightweight network monitoring service that periodically pings configured hosts and stores results in SQLite for historical analysis.

## Setup

```bash
pip install -e .
```

## Usage

```bash
ping-monitor run                        # Start monitoring
ping-monitor query                      # Query history
ping-monitor query --host 8.8.8.8 --hours 24  # Filter by host/time
ping-monitor stats                      # Aggregated statistics
ping-monitor ping                       # Single test ping
```

## Configuration

Edit `src/config.toml`:

```toml
[monitor]
interval_seconds = 60
retention_days = 30

[[hosts]]
address = "8.8.8.8"
name = "Google DNS"

[[hosts]]
address = "1.1.1.1"
name = "Cloudflare DNS"
```

## Service

```bash
systemctl --user enable --now ping-monitor
journalctl --user -u ping-monitor -f
```

Tracks latency, packet loss, and jitter per host.
