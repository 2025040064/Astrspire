"""Explicit offline measurement: usable projection and paint reported separately."""

import argparse
import json
from pathlib import Path
import statistics
import time

from PySide6.QtWidgets import QApplication, QWidget

from qx_stc_isp.vofa.channel_settings import ChannelDisplay
from qx_stc_isp.vofa.dashboard import DashboardManager
from qx_stc_isp.vofa.model import TelemetryEvent
from qx_stc_isp.vofa.sample_store import SampleStore


def measure(channels, app, rounds):
    store = SampleStore()
    manager = DashboardManager()
    manager.add_page("Performance")
    cards = [manager.add_card("waveform") for _ in range(3)]
    for card in cards:
        manager.card_widget(card).resize(1000, 300)
    config = (ChannelDisplay(),) * channels
    names = tuple(f"CH{i + 1}" for i in range(channels))
    for index in range(100000):
        store.append_event(TelemetryEvent(raw=b"", samples=tuple(float(index + ch) for ch in range(channels))))
    def request():
        target = store.cursor.sequence
        start = time.perf_counter()
        manager.request_projection(store, config, names)
        while any(manager.card_widget(card).displayed_sample_count != target for card in cards):
            app.processEvents()
            if time.perf_counter() - start > 15:
                raise TimeoutError("Projection did not complete")
            time.sleep(.001)
        return (time.perf_counter() - start) * 1000
    try:
        initial = request()
        def render():
            start = time.perf_counter()
            for card in cards:
                manager.card_widget(card).grab()
            return (time.perf_counter() - start) * 1000
        initial_render = render()
        latencies = []
        rendered_latencies = []
        combined_latencies = []
        for index in range(rounds):
            for step in range(250):
                store.append_event(TelemetryEvent(raw=b"", samples=(float(index * 250 + step),) * channels))
            projection_ms = request()
            rendered_ms = render()
            latencies.append(projection_ms)
            rendered_latencies.append(rendered_ms)
            combined_latencies.append(projection_ms + rendered_ms)
        paint_start = time.perf_counter()
        for card in cards:
            wave = manager.card_widget(card)
            QWidget.grab(wave)
        paint_ms = (time.perf_counter() - paint_start) * 1000
        columns = manager.card_widget(cards[0]).displayed_series
        return dict(channels=channels, cards=3, retained=100000, initial_ms=initial,
                    rounds=rounds, usable_p50_ms=statistics.median(latencies),
                    usable_max_ms=max(latencies), paint_three_cards_ms=paint_ms,
                    initial_render_three_cards_ms=initial_render,
                    first_display_total_ms=initial + initial_render,
                    render_three_cards_p50_ms=statistics.median(rendered_latencies),
                    update_through_render_p50_ms=statistics.median(combined_latencies),
                    plot_buffer_shared=all(manager.card_widget(card).plot_buffer is
                        manager.card_widget(cards[0]).plot_buffer for card in cards),
                    shared=all(manager.card_widget(card).displayed_series is columns for card in cards),
                    includes_paint_in_usable=False)
    finally:
        if not manager.stop_projection():
            raise RuntimeError("Projection thread did not stop")
        manager.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=20)
    args = parser.parse_args()
    if not 3 <= args.rounds <= 100:
        parser.error("rounds must be 3–100")
    app = QApplication([])
    result = [measure(channels, app, args.rounds) for channels in (1, 16)]
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
