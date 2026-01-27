#!/usr/bin/env python3
"""
ping-monitor - Network latency and connectivity monitoring with history tracking

A lightweight service that periodically pings configured hosts and stores
results in a SQLite database for historical analysis.
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import tomli

# Paths
TOOL_DIR = Path(__file__).parent
CONFIG_PATH = TOOL_DIR / "config.toml"
DB_PATH = TOOL_DIR / "history.db"


@dataclass
class PingResult:
    """Result of a single ping test."""
    host: str
    timestamp: datetime
    packets_sent: int
    packets_received: int
    packet_loss_percent: float
    min_ms: Optional[float]
    avg_ms: Optional[float]
    max_ms: Optional[float]
    jitter_ms: Optional[float]  # mdev in ping output

    @property
    def success(self) -> bool:
        return self.packets_received > 0


def load_config() -> dict:
    """Load configuration from TOML file."""
    if not CONFIG_PATH.exists():
        return {
            "targets": {"hosts": ["8.8.8.8", "1.1.1.1"]},
            "schedule": {"interval_seconds": 60, "packets": 3, "timeout": 5},
            "storage": {"retention_days": 30}
        }

    with open(CONFIG_PATH, "rb") as f:
        return tomli.load(f)


def init_database():
    """Initialize SQLite database with schema."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ping_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            host TEXT NOT NULL,
            packets_sent INTEGER NOT NULL,
            packets_received INTEGER NOT NULL,
            packet_loss_percent REAL NOT NULL,
            min_ms REAL,
            avg_ms REAL,
            max_ms REAL,
            jitter_ms REAL
        )
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp ON ping_results(timestamp)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_host ON ping_results(host)
    """)

    conn.commit()
    conn.close()


def run_ping(host: str, count: int = 3, timeout: int = 5) -> PingResult:
    """
    Execute ping command and parse results.

    Args:
        host: Target host (IP or hostname)
        count: Number of packets to send
        timeout: Timeout in seconds

    Returns:
        PingResult with parsed statistics
    """
    timestamp = datetime.now()

    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout), host],
            capture_output=True,
            text=True,
            timeout=timeout * count + 5
        )
        output = result.stdout + result.stderr

        # Parse packet statistics
        # "3 packets transmitted, 3 received, 0% packet loss"
        packet_match = re.search(
            r'(\d+) packets transmitted, (\d+) received.*?(\d+(?:\.\d+)?)% packet loss',
            output
        )

        if packet_match:
            packets_sent = int(packet_match.group(1))
            packets_received = int(packet_match.group(2))
            packet_loss = float(packet_match.group(3))
        else:
            packets_sent = count
            packets_received = 0
            packet_loss = 100.0

        # Parse timing statistics
        # "rtt min/avg/max/mdev = 1.234/5.678/9.012/1.234 ms"
        time_match = re.search(
            r'rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)',
            output
        )

        if time_match:
            min_ms = float(time_match.group(1))
            avg_ms = float(time_match.group(2))
            max_ms = float(time_match.group(3))
            jitter_ms = float(time_match.group(4))
        else:
            min_ms = avg_ms = max_ms = jitter_ms = None

        return PingResult(
            host=host,
            timestamp=timestamp,
            packets_sent=packets_sent,
            packets_received=packets_received,
            packet_loss_percent=packet_loss,
            min_ms=min_ms,
            avg_ms=avg_ms,
            max_ms=max_ms,
            jitter_ms=jitter_ms
        )

    except subprocess.TimeoutExpired:
        return PingResult(
            host=host,
            timestamp=timestamp,
            packets_sent=count,
            packets_received=0,
            packet_loss_percent=100.0,
            min_ms=None,
            avg_ms=None,
            max_ms=None,
            jitter_ms=None
        )


def store_result(result: PingResult):
    """Store ping result in database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO ping_results
        (timestamp, host, packets_sent, packets_received, packet_loss_percent,
         min_ms, avg_ms, max_ms, jitter_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result.timestamp.isoformat(),
        result.host,
        result.packets_sent,
        result.packets_received,
        result.packet_loss_percent,
        result.min_ms,
        result.avg_ms,
        result.max_ms,
        result.jitter_ms
    ))

    conn.commit()
    conn.close()


def cleanup_old_records(retention_days: int):
    """Remove records older than retention period."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
    cursor.execute("DELETE FROM ping_results WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount

    conn.commit()
    conn.close()

    return deleted


def run_service():
    """Main service loop - ping hosts at configured interval."""
    config = load_config()
    init_database()

    hosts = config.get("targets", {}).get("hosts", ["8.8.8.8"])
    interval = config.get("schedule", {}).get("interval_seconds", 60)
    packets = config.get("schedule", {}).get("packets", 3)
    timeout = config.get("schedule", {}).get("timeout", 5)
    retention = config.get("storage", {}).get("retention_days", 30)

    print(f"Starting ping monitor - {len(hosts)} hosts, {interval}s interval")

    cleanup_counter = 0

    while True:
        for host in hosts:
            result = run_ping(host, count=packets, timeout=timeout)
            store_result(result)

            status = "OK" if result.success else "FAIL"
            latency = f"{result.avg_ms:.1f}ms" if result.avg_ms else "N/A"
            print(f"[{result.timestamp.strftime('%H:%M:%S')}] {host}: {status} - {latency}")

        # Cleanup once per hour
        cleanup_counter += 1
        if cleanup_counter >= 3600 // interval:
            deleted = cleanup_old_records(retention)
            if deleted > 0:
                print(f"Cleaned up {deleted} old records")
            cleanup_counter = 0

        time.sleep(interval)


def query_history(
    host: Optional[str] = None,
    hours: int = 24,
    format: str = "table"
) -> str:
    """Query ping history from database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    if host:
        cursor.execute("""
            SELECT * FROM ping_results
            WHERE timestamp > ? AND host = ?
            ORDER BY timestamp DESC
        """, (cutoff, host))
    else:
        cursor.execute("""
            SELECT * FROM ping_results
            WHERE timestamp > ?
            ORDER BY timestamp DESC
        """, (cutoff,))

    rows = cursor.fetchall()
    conn.close()

    if format == "json":
        return json.dumps([dict(row) for row in rows], indent=2)

    # Table format
    if not rows:
        return "No data found for the specified period."

    lines = ["Timestamp            | Host           | Loss% | Avg(ms) | Jitter"]
    lines.append("-" * 70)

    for row in rows[:50]:  # Limit output
        ts = row["timestamp"][:19]
        host_str = row["host"][:14].ljust(14)
        loss = f"{row['packet_loss_percent']:5.1f}"
        avg = f"{row['avg_ms']:7.1f}" if row["avg_ms"] else "    N/A"
        jitter = f"{row['jitter_ms']:.1f}" if row["jitter_ms"] else "N/A"
        lines.append(f"{ts} | {host_str} | {loss} | {avg} | {jitter}")

    if len(rows) > 50:
        lines.append(f"... and {len(rows) - 50} more rows")

    return "\n".join(lines)


def get_stats(host: Optional[str] = None, hours: int = 24) -> dict:
    """Get aggregate statistics for a time period."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    query = """
        SELECT
            host,
            COUNT(*) as samples,
            AVG(packet_loss_percent) as avg_loss,
            AVG(avg_ms) as avg_latency,
            MIN(min_ms) as min_latency,
            MAX(max_ms) as max_latency,
            AVG(jitter_ms) as avg_jitter,
            SUM(CASE WHEN packet_loss_percent = 100 THEN 1 ELSE 0 END) as failed_tests
        FROM ping_results
        WHERE timestamp > ?
    """

    if host:
        query += " AND host = ?"
        cursor.execute(query + " GROUP BY host", (cutoff, host))
    else:
        cursor.execute(query + " GROUP BY host", (cutoff,))

    results = {}
    for row in cursor.fetchall():
        results[row[0]] = {
            "samples": row[1],
            "avg_loss_percent": round(row[2], 2) if row[2] else None,
            "avg_latency_ms": round(row[3], 2) if row[3] else None,
            "min_latency_ms": round(row[4], 2) if row[4] else None,
            "max_latency_ms": round(row[5], 2) if row[5] else None,
            "avg_jitter_ms": round(row[6], 2) if row[6] else None,
            "failed_tests": row[7],
            "uptime_percent": round(100 - (row[7] / row[1] * 100), 2) if row[1] else None
        }

    conn.close()
    return results


def main():
    """Main entry point with CLI."""
    parser = argparse.ArgumentParser(
        description="Network latency and connectivity monitoring"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Service command
    subparsers.add_parser("run", help="Run monitoring service")

    # Query command
    query_parser = subparsers.add_parser("query", help="Query history")
    query_parser.add_argument("--host", help="Filter by host")
    query_parser.add_argument("--hours", type=int, default=24, help="Hours to look back")
    query_parser.add_argument("--format", choices=["table", "json"], default="table")

    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show statistics")
    stats_parser.add_argument("--host", help="Filter by host")
    stats_parser.add_argument("--hours", type=int, default=24, help="Hours to look back")

    # Single ping command
    ping_parser = subparsers.add_parser("ping", help="Run single ping test")
    ping_parser.add_argument("host", help="Host to ping")
    ping_parser.add_argument("--count", type=int, default=3)

    # Init command
    subparsers.add_parser("init", help="Initialize database")

    args = parser.parse_args()

    if args.command == "run":
        run_service()
    elif args.command == "query":
        print(query_history(host=args.host, hours=args.hours, format=args.format))
    elif args.command == "stats":
        stats = get_stats(host=args.host, hours=args.hours)
        print(json.dumps(stats, indent=2))
    elif args.command == "ping":
        result = run_ping(args.host, count=args.count)
        print(f"Host: {result.host}")
        print(f"Packets: {result.packets_received}/{result.packets_sent}")
        print(f"Loss: {result.packet_loss_percent}%")
        if result.avg_ms:
            print(f"Latency: {result.min_ms:.1f}/{result.avg_ms:.1f}/{result.max_ms:.1f} ms")
            print(f"Jitter: {result.jitter_ms:.1f} ms")
    elif args.command == "init":
        init_database()
        print(f"Database initialized at {DB_PATH}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
