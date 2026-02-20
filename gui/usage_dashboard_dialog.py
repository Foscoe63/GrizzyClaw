"""Usage dashboard: tokens, costs, speed, and per-workspace LLM metrics."""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QGroupBox, QFormLayout, QWidget, QFrame,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont


def _get_theme_colors(parent):
    theme = getattr(getattr(parent, "settings", None), "theme", "Light") if parent else "Light"
    dark = theme in ("Dark", "High Contrast Dark", "Dracula", "Monokai", "Nord", "Solarized Dark")
    if dark:
        return {"bg": "#1C1C1E", "fg": "#FFFFFF", "border": "#3A3A3C", "secondary": "#8E8E93"}
    return {"bg": "#FFFFFF", "fg": "#1C1C1E", "border": "#E5E5EA", "secondary": "#8E8E93"}


def _estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """Rough cost estimate (placeholder rates per 1M tokens)."""
    # Example: $0.50/1M in, $1.50/1M out (adjust per provider)
    return (tokens_in / 1_000_000) * 0.5 + (tokens_out / 1_000_000) * 1.5


class UsageDashboardDialog(QDialog):
    """Dialog showing usage metrics, costs, speed, and per-workspace stats."""

    def __init__(self, workspace_manager, settings, parent=None):
        super().__init__(parent)
        self.workspace_manager = workspace_manager
        self.settings = settings
        self._colors = _get_theme_colors(parent)
        self.setWindowTitle("Usage & Performance")
        self.setMinimumSize(640, 480)
        self.setStyleSheet(f"QDialog {{ background-color: {self._colors['bg']}; }}")
        self.setup_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(2000)
        self.refresh()

    def closeEvent(self, event):
        self._refresh_timer.stop()
        super().closeEvent(event)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        c = self._colors

        header = QLabel("Usage Dashboard")
        header.setFont(QFont("-apple-system", 20, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {c['fg']};")
        layout.addWidget(header)

        # Global metrics
        global_group = QGroupBox("Global (this session)")
        global_group.setStyleSheet(f"QGroupBox {{ color: {c['fg']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 12px; margin-top: 8px; }}")
        global_layout = QFormLayout(global_group)
        self.tokens_in_lbl = QLabel("—")
        self.tokens_out_lbl = QLabel("—")
        self.latency_mean_lbl = QLabel("—")
        self.latency_p99_lbl = QLabel("—")
        self.error_rate_lbl = QLabel("—")
        self.cost_estimate_lbl = QLabel("—")
        for lbl in (self.tokens_in_lbl, self.tokens_out_lbl, self.latency_mean_lbl,
                    self.latency_p99_lbl, self.error_rate_lbl, self.cost_estimate_lbl):
            lbl.setStyleSheet(f"color: {c['fg']};")
        global_layout.addRow("Tokens in:", self.tokens_in_lbl)
        global_layout.addRow("Tokens out:", self.tokens_out_lbl)
        global_layout.addRow("Latency (mean):", self.latency_mean_lbl)
        global_layout.addRow("Latency (p99):", self.latency_p99_lbl)
        global_layout.addRow("Error rate:", self.error_rate_lbl)
        global_layout.addRow("Est. cost:", self.cost_estimate_lbl)
        layout.addWidget(global_group)

        # Per-workspace metrics
        ws_group = QGroupBox("Per-workspace (speed / quality)")
        ws_group.setStyleSheet(f"QGroupBox {{ color: {c['fg']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 12px; margin-top: 8px; }}")
        ws_layout = QVBoxLayout(ws_group)
        self.ws_table = QTableWidget()
        self.ws_table.setColumnCount(5)
        self.ws_table.setHorizontalHeaderLabels(["Workspace", "Messages", "Avg response (ms)", "Total tokens", "Quality %"])
        self.ws_table.setStyleSheet(f"QTableWidget {{ background: {c['bg']}; color: {c['fg']}; gridline-color: {c['border']}; }}")
        self.ws_table.horizontalHeader().setStretchLastSection(True)
        ws_layout.addWidget(self.ws_table)
        layout.addWidget(ws_group)

        # Benchmark button
        btn_row = QHBoxLayout()
        self.benchmark_btn = QPushButton("Run benchmark (active workspace)")
        self.benchmark_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.benchmark_btn.clicked.connect(self._run_benchmark)
        btn_row.addWidget(self.benchmark_btn)
        self.benchmark_result_lbl = QLabel("")
        self.benchmark_result_lbl.setStyleSheet(f"color: {c['secondary']};")
        btn_row.addWidget(self.benchmark_result_lbl)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

    def refresh(self):
        try:
            from grizzyclaw.observability.metrics import get_metrics
            stats = get_metrics().get_stats()
        except Exception:
            stats = {"llm": {}, "agent": {}}
        llm = stats.get("llm", {})
        tokens_in = llm.get("tokens_in_total", 0)
        tokens_out = llm.get("tokens_out_total", 0)
        self.tokens_in_lbl.setText(f"{tokens_in:,}")
        self.tokens_out_lbl.setText(f"{tokens_out:,}")
        mean_sec = llm.get("latency_mean_sec", 0)
        p99_sec = llm.get("latency_p99_sec", 0)
        self.latency_mean_lbl.setText(f"{mean_sec*1000:.0f} ms" if mean_sec else "—")
        self.latency_p99_lbl.setText(f"{p99_sec*1000:.0f} ms" if p99_sec else "—")
        err = llm.get("error_rate", 0)
        self.error_rate_lbl.setText(f"{err*100:.1f}%" if isinstance(err, (int, float)) else "—")
        cost = _estimate_cost(tokens_in, tokens_out)
        self.cost_estimate_lbl.setText(f"${cost:.4f}" if (tokens_in or tokens_out) else "—")

        if self.workspace_manager:
            ws_stats = self.workspace_manager.get_workspace_stats()
            workspaces = ws_stats.get("workspaces", [])
            self.ws_table.setRowCount(len(workspaces))
            for row, ws in enumerate(workspaces):
                self.ws_table.setItem(row, 0, QTableWidgetItem(f"{ws.get('icon', '')} {ws.get('name', ws.get('id', ''))}"))
                self.ws_table.setItem(row, 1, QTableWidgetItem(str(ws.get("message_count", 0))))
                self.ws_table.setItem(row, 2, QTableWidgetItem(f"{ws.get('avg_response_time_ms', 0):.0f}"))
                self.ws_table.setItem(row, 3, QTableWidgetItem(f"{ws.get('total_tokens', 0):,}"))
                self.ws_table.setItem(row, 4, QTableWidgetItem(f"{ws.get('quality_score', 0):.1f}"))
        else:
            self.ws_table.setRowCount(0)

    def _run_benchmark(self):
        self.benchmark_result_lbl.setText("Running…")
        self.benchmark_btn.setEnabled(False)
        active = self.workspace_manager.get_active_workspace() if self.workspace_manager else None
        if not active:
            self.benchmark_result_lbl.setText("No active workspace.")
            self.benchmark_btn.setEnabled(True)
            return
        agent = self.workspace_manager.get_or_create_agent(active.id, self.settings)
        if not agent:
            self.benchmark_result_lbl.setText("Could not create agent.")
            self.benchmark_btn.setEnabled(True)
            return

        from PyQt6.QtCore import QThread, pyqtSignal
        import time
        import asyncio

        class BenchmarkWorker(QThread):
            done = pyqtSignal(float, int, str)  # elapsed_ms, approx_tokens, error_or_empty

            def run(self):
                t0 = time.perf_counter()
                err = ""
                approx = 0
                try:
                    async def consume():
                        nonlocal approx
                        async for chunk in agent.process_message(
                            "benchmark_user", "Reply with exactly: OK"
                        ):
                            approx += len(chunk.split())
                    asyncio.run(consume())
                except Exception as e:
                    err = str(e)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                self.done.emit(elapsed_ms, approx, err)

        w = BenchmarkWorker(self)
        def on_done(elapsed_ms, approx, err):
            if err:
                self.benchmark_result_lbl.setText(f"Error: {err[:50]}")
            else:
                self.benchmark_result_lbl.setText(f"Done: {elapsed_ms:.0f} ms, ~{approx} tokens")
            self.benchmark_btn.setEnabled(True)
            self.refresh()
        w.done.connect(on_done)
        w.start()
        self._benchmark_worker = w
