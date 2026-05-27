from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple

import gradio as gr

from webui.e720_sweep import STANDARD_FREQUENCIES, SWEEP_MODE_LABELS
from webui.program_steps import STEP_STATIC_COLUMNS, STEP_TABLE_DATATYPES, STEP_TABLE_HEADERS

if TYPE_CHECKING:
    from webui.node import WebHMINode


def _freq_choices() -> List[str]:
    return [str(f) for f in STANDARD_FREQUENCIES]


def _sweep_mode_choices() -> List[Tuple[str, int]]:
    return [(SWEEP_MODE_LABELS[k], k) for k in sorted(SWEEP_MODE_LABELS)]


def _build_e720_block() -> Tuple[gr.Dropdown, gr.CheckboxGroup, gr.Number]:
    sweep_mode = gr.Dropdown(choices=_sweep_mode_choices(), value=0, label='Sweep mode')
    enabled_freqs = gr.CheckboxGroup(choices=_freq_choices(), value=['1000'], label='Enabled frequencies [Hz]')
    range_max = gr.Number(label='Range max [Hz]', value=10000, precision=0)
    return sweep_mode, enabled_freqs, range_max


def build_program_new_page(node: 'WebHMINode') -> None:
    gr.Markdown('# New program')
    gr.Button('← Back to program list', size='lg', link='programs')

    description = gr.Textbox(
        label='Description (optional)',
        lines=4,
        placeholder='What is this experiment for?',
    )

    gr.Markdown('### Temperature steps')
    gr.Markdown(
        'Press **Add step** to insert a row. Click **t_start_k**, **t_stop_k**, or **minutes** to edit. '
        'Click **🗑** on a row to remove it.'
    )

    draft_steps = gr.State([])

    steps_table = gr.Dataframe(
        headers=STEP_TABLE_HEADERS,
        datatype=STEP_TABLE_DATATYPES,
        value=[],
        type='array',
        interactive=True,
        static_columns=STEP_STATIC_COLUMNS,
        max_height=420,
        label='Steps (saved when you press Create program)',
        column_widths=['8%', '24%', '24%', '24%', '10%'],
    )
    add_step_btn = gr.Button('Add step', size='lg', variant='primary')
    steps_msg = gr.Textbox(label='Steps', interactive=False, lines=1)

    sweep_mode, enabled_freqs, range_max = _build_e720_block()
    form_msg = gr.Textbox(label='Result', interactive=False, lines=2)

    with gr.Row():
        create_save_btn = gr.Button('Create program', variant='primary', size='lg')
        create_cancel_btn = gr.Button('Cancel', size='lg', link='programs')

    add_step_btn.click(
        node.ui_program_draft_add_row,
        inputs=[steps_table, draft_steps],
        outputs=[draft_steps, steps_table, steps_msg],
    )
    steps_table.change(
        node.ui_program_draft_sync_table,
        inputs=[steps_table, draft_steps],
        outputs=[draft_steps, steps_msg],
    )
    steps_table.select(
        node.ui_program_draft_delete_row,
        inputs=[steps_table, draft_steps],
        outputs=[draft_steps, steps_table, steps_msg],
    )

    create_save_btn.click(
        node.ui_program_create_save_new_page,
        inputs=[description, steps_table, draft_steps, sweep_mode, enabled_freqs, range_max],
        outputs=[form_msg],
    )
