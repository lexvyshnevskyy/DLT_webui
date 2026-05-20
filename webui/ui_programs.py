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


def build_programs_list(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown('# Programs')
    gr.Markdown(
        'Manage experiment programs. Select a row in the table, then use the large buttons below. '
        '[Create new program](/program-new)'
    )

    with gr.Row():
        refresh_btn = gr.Button('Refresh list', variant='secondary')
        new_link = gr.Button('➕ Create new program', link='/program-new', variant='primary', size='lg')

    programs_table = gr.Dataframe(
        headers=['id', 'datetime', 'status'],
        datatype=['number', 'str', 'str'],
        interactive=False,
        label='All programs',
    )
    list_message = gr.Textbox(label='Status', interactive=False, lines=2)

    selected_program = gr.Dropdown(label='Selected program', choices=[], value=None)

    with gr.Row():
        show_btn = gr.Button('Show details', size='lg')
        edit_btn = gr.Button('Edit program', size='lg', variant='primary')
        export_btn = gr.Button('Export ZIP', size='lg')
        delete_btn = gr.Button('Delete program', size='lg', variant='stop')

    with gr.Column(visible=False) as delete_dialog:
        gr.Markdown('### Delete this program?')
        delete_warning = gr.Markdown('')
        with gr.Row():
            delete_confirm_btn = gr.Button('Yes, delete everything', variant='stop', size='lg')
            delete_cancel_btn = gr.Button('Cancel', size='lg')

    export_file = gr.File(label='Download export', interactive=False)
    export_message = gr.Textbox(label='Export result', interactive=False)

    load_outputs = [programs_table, list_message, selected_program]

    refresh_btn.click(node.ui_programs_list_refresh, outputs=load_outputs)
    new_link.click(lambda: node.ui_programs_set_nav(0, 'create'), outputs=[])
    show_btn.click(
        node.ui_programs_prepare_show,
        inputs=[selected_program],
        js="() => { window.location.href = '/program-view'; }",
    )
    edit_btn.click(
        node.ui_programs_prepare_edit,
        inputs=[selected_program],
        js="() => { window.location.href = '/program-edit'; }",
    )
    delete_btn.click(
        node.ui_programs_delete_dialog_show,
        inputs=[selected_program],
        outputs=[delete_dialog, delete_warning],
    )
    delete_cancel_btn.click(
        lambda: (gr.update(visible=False), ''),
        outputs=[delete_dialog, delete_warning],
    )
    delete_confirm_btn.click(
        node.ui_programs_delete_confirmed,
        inputs=[selected_program],
        outputs=[programs_table, list_message, selected_program, delete_dialog, delete_warning],
    )
    export_btn.click(
        node.ui_programs_export,
        inputs=[selected_program],
        outputs=[export_file, export_message],
    )

    return load_outputs


def _build_e720_section() -> Tuple[gr.components.Component, ...]:
    gr.Markdown('### E7-20 frequency profile (Delphi-style)')
    sweep_mode = gr.Dropdown(choices=_sweep_mode_choices(), value=0, label='Sweep mode')
    enabled_freqs = gr.CheckboxGroup(choices=_freq_choices(), value=['1000'], label='Enabled frequencies [Hz]')
    range_max = gr.Number(label='Range max [Hz]', value=10000, precision=0)
    return sweep_mode, enabled_freqs, range_max


def build_program_create(node: 'WebHMINode') -> None:
    gr.Markdown('# New program')
    gr.Button('← Back to programs', link='/programs')

    description = gr.Textbox(label='Description (optional)', lines=4, placeholder='What is this experiment for?')
    gr.Markdown('### Temperature steps')
    steps_table = gr.Dataframe(
        headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
        datatype=['number', 'number', 'number', 'number'],
        interactive=False,
        label='Steps (saved when you create the program)',
    )
    with gr.Row():
        t_start = gr.Number(label='T start [K]', value=300.0)
        t_stop = gr.Number(label='T stop [K]', value=350.0)
        minutes = gr.Number(label='Minutes', value=10.0)
    add_step_btn = gr.Button('Add step to list')
    steps_msg = gr.Textbox(label='Steps message', interactive=False)

    sweep_mode, enabled_freqs, range_max = _build_e720_section()

    form_msg = gr.Textbox(label='Result', interactive=False, lines=2)
    with gr.Row():
        create_btn = gr.Button('Create program', variant='primary', size='lg')
        gr.Button('Cancel', link='/programs', size='lg')

    create_btn.click(
        node.ui_program_create_save,
        inputs=[description, steps_table, sweep_mode, enabled_freqs, range_max],
        outputs=[form_msg],
    )
    add_step_btn.click(
        node.ui_program_draft_add_step,
        inputs=[steps_table, t_start, t_stop, minutes],
        outputs=[steps_table, steps_msg],
    )


def build_program_edit(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown('# Edit program')
    gr.Button('← Back to programs', link='/programs')

    program_header = gr.Markdown('')
    description = gr.Textbox(label='Description', lines=4)
    status_box = gr.Textbox(label='Program status', interactive=False)

    gr.Markdown('### Temperature steps')
    steps_table = gr.Dataframe(
        headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
        datatype=['number', 'number', 'number', 'number'],
        interactive=False,
    )
    with gr.Row():
        t_start = gr.Number(label='T start [K]', value=300.0)
        t_stop = gr.Number(label='T stop [K]', value=350.0)
        minutes = gr.Number(label='Minutes', value=10.0)
    with gr.Row():
        add_step_btn = gr.Button('Add step')
        delete_step_id = gr.Number(label='Step ID to remove', precision=0, value=0)
        delete_step_btn = gr.Button('Remove step')
    steps_msg = gr.Textbox(label='Steps message', interactive=False)

    sweep_mode, enabled_freqs, range_max = _build_e720_section()

    gr.Markdown('### Run experiment')
    with gr.Row():
        run_btn = gr.Button('▶ Run program', variant='primary', size='lg')
        stop_btn = gr.Button('■ Stop', variant='stop', size='lg')
    run_status = gr.Textbox(label='Run status', interactive=False)

    form_msg = gr.Textbox(label='Save result', interactive=False, lines=2)
    with gr.Row():
        save_btn = gr.Button('Save changes', variant='primary', size='lg')
        gr.Button('Cancel', link='/programs', size='lg')

    load_outputs = [
        program_header,
        description,
        status_box,
        steps_table,
        sweep_mode,
        enabled_freqs,
        range_max,
        run_status,
    ]

    save_btn.click(
        node.ui_program_edit_save,
        inputs=[description, sweep_mode, enabled_freqs, range_max],
        outputs=[form_msg],
    )
    add_step_btn.click(
        node.ui_program_edit_add_step,
        inputs=[t_start, t_stop, minutes],
        outputs=[steps_table, steps_msg],
    )
    delete_step_btn.click(
        node.ui_program_edit_delete_step,
        inputs=[delete_step_id],
        outputs=[steps_table, steps_msg],
    )
    run_btn.click(node.ui_start_program_from_edit, outputs=[run_status, form_msg])
    stop_btn.click(node.ui_stop_program_from_edit, outputs=[run_status, form_msg])

    return load_outputs


def build_program_view(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown('# Program details')
    with gr.Row():
        gr.Button('← Back to programs', link='/programs')
        edit_link = gr.Button('Edit this program', variant='primary', size='lg')

    summary = gr.Markdown('')
    steps_table = gr.Dataframe(
        headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
        interactive=False,
    )
    e720_box = gr.Code(label='E7-20 configuration', language='json')
    stats_box = gr.Code(label='Measurement statistics', language='json')
    view_msg = gr.Textbox(label='Status', interactive=False)

    load_outputs = [summary, steps_table, e720_box, stats_box, view_msg]

    edit_link.click(
        node.ui_programs_prepare_edit_from_view,
        js="() => { window.location.href = '/program-edit'; }",
    )

    return load_outputs
