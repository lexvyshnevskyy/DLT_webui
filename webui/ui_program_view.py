from __future__ import annotations

from typing import TYPE_CHECKING, List

import gradio as gr

if TYPE_CHECKING:
    from webui.node import WebHMINode


def build_program_view_page(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown('# Program details')
    with gr.Row():
        gr.Button('← Back to program list', size='lg', link='programs')
        view_edit_btn = gr.Button('Edit this program', variant='primary', size='lg')

    view_summary = gr.Markdown('')
    view_steps_table = gr.Dataframe(
        headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
        interactive=False,
    )
    view_e720_box = gr.Code(label='E7-20 configuration', language='json')
    view_stats_box = gr.Code(label='Measurement statistics', language='json')
    view_msg = gr.Textbox(label='Status', interactive=False)

    return [view_summary, view_steps_table, view_e720_box, view_stats_box, view_msg, view_edit_btn]
