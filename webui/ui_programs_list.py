from __future__ import annotations

from typing import TYPE_CHECKING, List

import gradio as gr

from webui.programs_html import PROGRAMS_TABLE_CSS

if TYPE_CHECKING:
    from webui.node import WebHMINode

MAX_PROGRAM_ROWS = 48
SLOTS_PER_ROW = 6


def apply_program_list_rows(rows: List) -> List[gr.update]:
    """Update row slot visibility, labels, and navigation links."""
    updates: List[gr.update] = []
    for i in range(MAX_PROGRAM_ROWS):
        if i < len(rows):
            pid = int(rows[i][0])
            dt = str(rows[i][1])
            st = str(rows[i][2])
            view_link = f'program-view?id={pid}'
            edit_link = f'program-edit?id={pid}'
            updates.extend([
                gr.update(visible=True),
                gr.update(value=pid),
                gr.update(value=str(pid), link=view_link),
                gr.update(value=dt, link=view_link),
                gr.update(value=st, link=view_link),
                gr.update(visible=True, link=edit_link),
            ])
        else:
            updates.extend([
                gr.update(visible=False),
                gr.update(value=0),
                gr.update(value=''),
                gr.update(value=''),
                gr.update(value=''),
                gr.update(visible=False),
            ])
    return updates


def build_programs_list_page(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.HTML(f'<style>{PROGRAMS_TABLE_CSS}</style>')

    programs_rows = gr.State([])
    list_message = gr.Textbox(label='Status', interactive=False, lines=2)
    export_file = gr.File(label='Download export', interactive=False)
    export_message = gr.Textbox(label='Export result', interactive=False, lines=1)
    row_slot_outputs: List[gr.components.Component] = []

    gr.Markdown('# Programs')
    with gr.Row():
        refresh_btn = gr.Button('Refresh list', variant='secondary')
        new_btn = gr.Button('➕ Create new program', variant='primary', size='lg', link='/program/new')

    with gr.Column(elem_classes=['del-prog-wrap']):
        with gr.Row(elem_classes=['del-prog-head']):
            gr.Markdown('id', elem_classes=['del-prog-head-label'])
            gr.Markdown('Date', elem_classes=['del-prog-head-label'])
            gr.Markdown('status', elem_classes=['del-prog-head-label'])
            gr.Markdown('', elem_classes=['del-prog-head-label'])
            gr.Markdown('', elem_classes=['del-prog-head-label'])
            gr.Markdown('', elem_classes=['del-prog-head-label'])

        for _ in range(MAX_PROGRAM_ROWS):
            with gr.Row(visible=False, elem_classes=['del-prog-row']) as row_box:
                pid_num = gr.Number(0, visible=False, precision=0)
                btn_id = gr.Button('', size='sm', variant='secondary', elem_classes=['del-prog-cell'])
                btn_date = gr.Button('', size='sm', variant='secondary', elem_classes=['del-prog-cell'])
                btn_status = gr.Button('', size='sm', variant='secondary', elem_classes=['del-prog-cell'])
                btn_edit = gr.Button('Edit program', size='sm', variant='secondary', elem_classes=['del-prog-action'])
                btn_export = gr.Button('Export ZIP', size='sm', variant='secondary', elem_classes=['del-prog-action'])
                btn_delete = gr.Button('Delete', size='sm', variant='stop', elem_classes=['del-prog-action', 'del-prog-delete'])

                btn_export.click(node.ui_programs_export_row, inputs=[pid_num], outputs=[export_file, export_message])
                btn_delete.click(node.ui_programs_delete_row, inputs=[pid_num], outputs=[programs_rows, list_message]).then(
                    apply_program_list_rows,
                    inputs=[programs_rows],
                    outputs=row_slot_outputs,
                )

                row_slot_outputs.extend([row_box, pid_num, btn_id, btn_date, btn_status, btn_edit])

    refresh_btn.click(node.ui_programs_list_refresh, outputs=[programs_rows, list_message]).then(
        apply_program_list_rows,
        inputs=[programs_rows],
        outputs=row_slot_outputs,
    )

    return [programs_rows, list_message, *row_slot_outputs]
