from __future__ import annotations

from typing import TYPE_CHECKING, List, Tuple

import gradio as gr

from webui.e720_sweep import STANDARD_FREQUENCIES, SWEEP_MODE_LABELS

if TYPE_CHECKING:
    from webui.node import WebHMINode


def _freq_choices() -> List[str]:
    return [str(f) for f in STANDARD_FREQUENCIES]


def _sweep_mode_choices() -> List[Tuple[str, int]]:
    return [(SWEEP_MODE_LABELS[k], k) for k in sorted(SWEEP_MODE_LABELS)]


def build_program_edit_page(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown('# Edit program')
    gr.Button('← Back to program list', size='lg', link='programs')

    edit_header = gr.Markdown('')
    edit_description = gr.Textbox(label='Description', lines=4)
    edit_status = gr.Textbox(label='Program status', interactive=False)

    gr.Markdown('### Temperature steps')
    edit_steps_table = gr.Dataframe(
        headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
        datatype=['number', 'number', 'number', 'number'],
        interactive=False,
    )
    with gr.Row():
        edit_t_start = gr.Number(label='T start [K]', value=300.0)
        edit_t_stop = gr.Number(label='T stop [K]', value=350.0)
        edit_minutes = gr.Number(label='Minutes', value=10.0)
    with gr.Row():
        edit_add_step_btn = gr.Button('Add step')
        edit_delete_step_id = gr.Number(label='Step ID to remove', precision=0, value=0)
        edit_delete_step_btn = gr.Button('Remove step')
    edit_steps_msg = gr.Textbox(label='Steps message', interactive=False)

    edit_sweep_mode = gr.Dropdown(choices=_sweep_mode_choices(), value=0, label='Sweep mode')
    edit_enabled_freqs = gr.CheckboxGroup(choices=_freq_choices(), value=['1000'], label='Enabled frequencies [Hz]')
    edit_range_max = gr.Number(label='Range max [Hz]', value=10000, precision=0)

    gr.Markdown('### Run experiment')
    with gr.Row():
        edit_run_btn = gr.Button('▶ Run program', variant='primary', size='lg')
        edit_stop_btn = gr.Button('■ Stop', variant='stop', size='lg')
    edit_run_status = gr.Textbox(label='Run status', interactive=False)
    edit_form_msg = gr.Textbox(label='Save result', interactive=False, lines=2)
    edit_save_btn = gr.Button('Save changes', variant='primary', size='lg')

    edit_save_btn.click(
        node.ui_program_edit_save,
        inputs=[edit_description, edit_sweep_mode, edit_enabled_freqs, edit_range_max],
        outputs=[edit_form_msg],
    )
    edit_add_step_btn.click(
        node.ui_program_edit_add_step,
        inputs=[edit_t_start, edit_t_stop, edit_minutes],
        outputs=[edit_steps_table, edit_steps_msg],
    )
    edit_delete_step_btn.click(
        node.ui_program_edit_delete_step,
        inputs=[edit_delete_step_id],
        outputs=[edit_steps_table, edit_steps_msg],
    )
    edit_run_btn.click(node.ui_start_program_from_edit, outputs=[edit_run_status, edit_form_msg])
    edit_stop_btn.click(node.ui_stop_program_from_edit, outputs=[edit_run_status, edit_form_msg])

    return [
        edit_header,
        edit_description,
        edit_status,
        edit_steps_table,
        edit_sweep_mode,
        edit_enabled_freqs,
        edit_range_max,
        edit_run_status,
    ]
