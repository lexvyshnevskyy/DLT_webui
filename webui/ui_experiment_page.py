from __future__ import annotations

from typing import TYPE_CHECKING, List

import gradio as gr

if TYPE_CHECKING:
    from webui.node import WebHMINode


def build_experiment_page(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown('Live data from **LTM2985** (`/ltm2985/measurement`) and **E7-20** (`/measure_device`). Refreshes every 1 s.')

    experiment_banner = gr.Textbox(label='Experiment state', interactive=False)
    with gr.Row():
        with gr.Column():
            gr.Markdown('### LTM2985 temperature')
            temp_summary = gr.Textbox(label='Summary', lines=8, interactive=False)
            measurement_table = gr.Dataframe(
                headers=['channel', 'type', 'value', 'valid', 'age_s'],
                interactive=False,
                label='LTM temperature channels',
            )
            ltm_stream = gr.Textbox(label='LTM stream (recent)', lines=12, interactive=False)
        with gr.Column():
            gr.Markdown('### E7-20 RCL meter')
            e720_summary = gr.Textbox(label='Live values', lines=10, interactive=False)
            e720_table = gr.Dataframe(
                headers=['online', 'im', 'v1', 'sec', 'v2', 'freq', 'level', 'offset', 'range'],
                interactive=False,
                label='Snapshot',
            )
            e720_stream = gr.Textbox(label='E7-20 stream (recent)', lines=12, interactive=False)

    with gr.Row():
        manual_enabled_box = gr.Checkbox(label='Manual temperature control', value=False)
        manual_target_box = gr.Number(label='Target [K]', value=373.15)
        manual_apply_btn = gr.Button('Apply')
    control_message = gr.Textbox(label='Control message', interactive=False)
    core_snapshot_box = gr.Code(label='Core control', language='json')

    manual_apply_btn.click(
        node.ui_manual_target,
        inputs=[manual_target_box, manual_enabled_box],
        outputs=[core_snapshot_box],
    )

    tick_outputs = [
        experiment_banner,
        temp_summary,
        measurement_table,
        ltm_stream,
        e720_summary,
        e720_table,
        e720_stream,
        core_snapshot_box,
    ]
    timer = gr.Timer(value=node.status_refresh_period_sec)
    timer.tick(node.ui_tick_experiment, outputs=tick_outputs)
    return tick_outputs
