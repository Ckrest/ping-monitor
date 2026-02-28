#!/usr/bin/env python3
"""
Ping Monitor Showcase

Renders a GTK4 window displaying real ping-monitor data from history.db
using graph-lib widgets. Screenshot this window for the README.

Usage:
    python showcase.py
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from graph_lib.widgets.graph_widget import GraphWidget
from graph_lib.renderers.line_chart import LineChartRenderer
from graph_lib.renderers.gauge import GaugeRenderer
from graph_lib.providers.sqlite_provider import SQLiteProvider
from graph_lib.providers.static_provider import StaticProvider
from graph_lib.providers.base import DataPoint

DB_PATH = Path(__file__).parent / "history.db"


def _hours_to_cover_last_day() -> int:
    """Return hours from now back to (newest_record - 24h) so we get a full day of data."""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM ping_results")
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return 24
    newest = datetime.fromisoformat(row[0])
    # We want a 24h window ending at the newest record
    start = newest - timedelta(hours=24)
    hours_from_now = int((datetime.now() - start).total_seconds() / 3600) + 1
    return hours_from_now


def calculate_uptime(hours: int) -> float:
    """Calculate uptime % over the last `hours` of data."""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM ping_results")
    row = cur.fetchone()
    if not row or not row[0]:
        conn.close()
        return 100.0
    newest = datetime.fromisoformat(row[0])
    cutoff = (newest - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT COUNT(*), SUM(CASE WHEN packet_loss_percent < 100 THEN 1 ELSE 0 END) "
        "FROM ping_results WHERE timestamp > ?",
        (cutoff,),
    )
    total, success = cur.fetchone()
    conn.close()
    if not total:
        return 100.0
    return (success / total) * 100


class ShowcaseWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title("Ping Monitor")
        self.set_default_size(900, 520)

        self._graphs = []
        self._hours = _hours_to_cover_last_day()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header = Adw.HeaderBar()
        main_box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        main_box.append(content)

        # --- Row 1: Main latency chart ---
        content.append(self._make_latency_chart())

        # --- Row 2: Packet loss, jitter, uptime gauge ---
        row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row2.set_homogeneous(False)
        row2.set_vexpand(True)
        content.append(row2)

        row2.append(self._make_packet_loss_chart())
        row2.append(self._make_jitter_chart())
        row2.append(self._make_uptime_gauge())

        for g in self._graphs:
            g.start()

    def _make_latency_chart(self):
        provider = SQLiteProvider(
            db_path=str(DB_PATH),
            table="ping_results",
            value_column="avg_ms",
            time_column="timestamp",
            time_range_hours=self._hours,
        )
        renderer = LineChartRenderer()
        renderer.configure(
            title="Network Latency",
            y_label="Latency",
            x_label="Time",
            unit=" ms",
            line_color=(0.208, 0.518, 0.894),
            show_fill=True,
            fill_color=(0.208, 0.518, 0.894, 0.2),
            y_min=0,
            show_grid=True,
            show_axes=True,
            show_y_ticks=True,
            show_x_ticks=True,
            show_current=True,
            current_position="top-right",
            current_format="{:.1f}",
        )
        graph = GraphWidget(renderer, provider, refresh_interval_ms=0)
        graph.set_size_request(-1, 250)
        self._graphs.append(graph)

        frame = Gtk.Frame()
        frame.set_child(graph)
        return frame

    def _make_packet_loss_chart(self):
        provider = SQLiteProvider(
            db_path=str(DB_PATH),
            table="ping_results",
            value_column="packet_loss_percent",
            time_column="timestamp",
            time_range_hours=self._hours,
        )
        renderer = LineChartRenderer()
        renderer.configure(
            title="Packet Loss",
            unit="%",
            line_color=(0.753, 0.110, 0.157),
            show_fill=True,
            fill_color=(0.753, 0.110, 0.157, 0.2),
            y_min=0,
            y_max=100,
            show_grid=True,
            show_axes=True,
            show_y_ticks=True,
            show_x_ticks=False,
        )
        graph = GraphWidget(renderer, provider, refresh_interval_ms=0)
        graph.set_hexpand(True)
        graph.set_vexpand(True)
        self._graphs.append(graph)

        frame = Gtk.Frame()
        frame.set_hexpand(True)
        frame.set_child(graph)
        return frame

    def _make_jitter_chart(self):
        provider = SQLiteProvider(
            db_path=str(DB_PATH),
            table="ping_results",
            value_column="jitter_ms",
            time_column="timestamp",
            time_range_hours=self._hours,
        )
        renderer = LineChartRenderer()
        renderer.configure(
            title="Jitter",
            unit=" ms",
            line_color=(0.204, 0.659, 0.325),
            show_fill=True,
            fill_color=(0.204, 0.659, 0.325, 0.2),
            y_min=0,
            show_grid=True,
            show_axes=True,
            show_y_ticks=True,
            show_x_ticks=False,
        )
        graph = GraphWidget(renderer, provider, refresh_interval_ms=0)
        graph.set_hexpand(True)
        graph.set_vexpand(True)
        self._graphs.append(graph)

        frame = Gtk.Frame()
        frame.set_hexpand(True)
        frame.set_child(graph)
        return frame

    def _make_uptime_gauge(self):
        uptime = calculate_uptime(self._hours)
        provider = StaticProvider(data=[DataPoint(timestamp=0, value=uptime)])
        renderer = GaugeRenderer()
        # For uptime, high = good. Set thresholds so only low values trigger warning/critical.
        renderer.configure(
            label="Uptime",
            min_value=0,
            max_value=100,
            warning_threshold=101,  # Never triggers — always green
            critical_threshold=102,
            normal_color=(0.204, 0.659, 0.325),
            value_format="{:.1f}%",
        )
        graph = GraphWidget(renderer, provider, refresh_interval_ms=0)
        graph.set_size_request(160, -1)
        graph.set_vexpand(True)
        self._graphs.append(graph)

        frame = Gtk.Frame()
        frame.set_child(graph)
        return frame


class ShowcaseApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.pingmonitor.showcase")

    def do_activate(self):
        win = ShowcaseWindow(application=self)
        win.present()


def main():
    app = ShowcaseApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
