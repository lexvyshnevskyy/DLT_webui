from __future__ import annotations

from typing import TYPE_CHECKING, List

import gradio as gr

from webui.e720_commands import PANEL_COMMANDS, command_choices
from webui.e720_sweep import STANDARD_FREQUENCIES, SWEEP_MODE_LABELS
from webui.ui_config_tab import build_configuration_tab
from webui.ui_experiment_page import build_experiment_page

if TYPE_CHECKING:
    from webui.node import WebHMINode


def build_ui(node: 'WebHMINode') -> gr.Blocks:
    command_labels = [label for label, _ in command_choices()]
    command_values = {label: value for label, value in command_choices()}
    freq_choices = [str(f) for f in STANDARD_FREQUENCIES]
    sweep_mode_choices = [(SWEEP_MODE_LABELS[k], k) for k in sorted(SWEEP_MODE_LABELS)]

    with gr.Blocks(title=node.title, theme=gr.themes.Soft()) as demo:
        gr.Navbar(
            value=[
                ('Experiment', '/experiment'),
                ('Configuration', '/configuration'),
            ],
            main_page_name='Dashboard',
        )
        gr.Markdown(f'# {node.title}')
        gr.Markdown('Dashboard — General status and Programs. Live experiment data is on **Experiment**.')

        with gr.Tab('General status'):
            with gr.Row():
                ros_health_box = gr.JSON(label='ROS services health')
                host_summary_box = gr.JSON(label='Host summary')
            services_table = gr.Dataframe(
                headers=['unit', 'active', 'sub_state', 'enabled', 'pid'],
                interactive=False,
                label='Systemd services',
            )
            with gr.Row():
                service_unit_box = gr.Dropdown(
                    choices=node.systemd_units,
                    value=node.systemd_units[0] if node.systemd_units else None,
                    label='Service',
                )
                service_start_btn = gr.Button('Start')
                service_stop_btn = gr.Button('Stop')
                service_restart_btn = gr.Button('Restart', variant='primary')
            service_action_msg = gr.Textbox(label='Service action result', interactive=False)
            disk_table = gr.Dataframe(
                headers=['device', 'mount', 'fstype', 'total_gb', 'used_gb', 'free_gb', 'used_%'],
                interactive=False,
                label='Disk usage',
            )
            uart_table = gr.Dataframe(
                headers=['device', 'path', 'exists', 'read', 'write'],
                interactive=False,
                label='UART devices',
            )
            net_overview_table = gr.Dataframe(
                headers=['interface', 'state', 'mac', 'ipv4', 'ipv6', 'speed_mbps'],
                interactive=False,
                label='Network interfaces',
            )
            logs_box = gr.Textbox(label='Event log', lines=10, interactive=False)

        with gr.Tab('Programs'):
            program_id_box = gr.Number(label='Program ID', precision=0, value=0)
            programs_table = gr.Dataframe(
                headers=['id', 'datetime', 'status'],
                datatype=['number', 'str', 'str'],
                interactive=False,
                label='Programs',
            )
            programs_message = gr.Textbox(label='Message', interactive=False)
            program_run_status = gr.Textbox(label='Experiment run status', interactive=False)
            program_stats_box = gr.Code(label='Measurement statistics', language='json')
            with gr.Row():
                create_program_btn = gr.Button('New')
                duplicate_program_btn = gr.Button('Duplicate')
                refresh_programs_btn = gr.Button('Refresh')
                load_program_btn = gr.Button('Load')
                delete_program_btn = gr.Button('Delete', variant='stop')
            steps_table = gr.Dataframe(
                headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
                datatype=['number', 'number', 'number', 'number'],
                interactive=False,
                label='Temperature steps',
            )
            steps_message = gr.Textbox(label='Steps message', interactive=False)
            with gr.Row():
                t_start_box = gr.Number(label='T start [K]', value=300.0)
                t_stop_box = gr.Number(label='T stop [K]', value=350.0)
                minutes_box = gr.Number(label='Minutes', value=10.0)
            with gr.Row():
                add_step_btn = gr.Button('Add step')
                delete_step_id_box = gr.Number(label='Step ID to delete', precision=0, value=0)
                delete_step_btn = gr.Button('Delete step')
            with gr.Row():
                start_btn = gr.Button('Run program', variant='primary')
                stop_btn = gr.Button('Stop', variant='stop')
                clear_meas_btn = gr.Button('Clear measurements')
            clear_meas_msg = gr.Textbox(label='Clear measurements result', interactive=False)
            with gr.Row():
                export_limit_box = gr.Number(label='Export row limit', value=50000, precision=0)
                export_clear_box = gr.Checkbox(label='Clear measurements before export', value=False)
                export_btn = gr.Button('Export ZIP')
            export_file = gr.File(label='Download', interactive=False)
            export_message = gr.Textbox(label='Export message', interactive=False)

            gr.Markdown('### E7-20 frequency profile (saved per program)')
            with gr.Row():
                sweep_mode_box = gr.Dropdown(
                    choices=sweep_mode_choices,
                    value=0,
                    label='Sweep mode',
                )
                enabled_freqs_box = gr.CheckboxGroup(choices=freq_choices, value=['1000'], label='Enabled frequencies [Hz]')
                range_max_box = gr.Number(label='Range max [Hz]', value=10000)
            save_e720_btn = gr.Button('Save E7-20 profile to program')
            save_e720_msg = gr.Textbox(label='E7-20 profile message', interactive=False)

            gr.Markdown('### E7-20 manual commands')
            with gr.Row():
                e720_cmd_dropdown = gr.Dropdown(
                    choices=command_labels,
                    value=command_labels[0] if command_labels else None,
                    label='Command',
                )
                e720_send_btn = gr.Button('Send')
                e720_custom_byte = gr.Number(label='Custom byte', precision=0, value=1)
                e720_send_custom_btn = gr.Button('Send byte')
            e720_cmd_message = gr.Textbox(label='Command result', interactive=False)
            gr.Markdown('Panel keys')
            panel_btns: List[gr.Button] = []
            panel_row = None
            for idx, label in enumerate(PANEL_COMMANDS):
                if idx % 8 == 0:
                    panel_row = gr.Row()
                with panel_row:
                    panel_btns.append(gr.Button(label))

        # --- Dashboard wiring ---
        for btn, action in [
            (service_start_btn, 'start'),
            (service_stop_btn, 'stop'),
            (service_restart_btn, 'restart'),
        ]:
            btn.click(
                lambda u, a=action: node.ui_service_control(u, a),
                inputs=[service_unit_box],
                outputs=[service_action_msg],
            )

        refresh_programs_btn.click(node.ui_refresh_programs, outputs=[programs_table, programs_message])
        create_program_btn.click(node.ui_create_program, outputs=[program_id_box, programs_table, programs_message])
        duplicate_program_btn.click(
            node.ui_duplicate_program,
            inputs=[program_id_box],
            outputs=[program_id_box, programs_table, programs_message],
        )
        load_program_btn.click(
            node.ui_load_program,
            inputs=[program_id_box],
            outputs=[steps_table, steps_message, program_stats_box],
        )
        delete_program_btn.click(
            node.ui_delete_program,
            inputs=[program_id_box],
            outputs=[programs_table, programs_message, program_id_box],
        )
        add_step_btn.click(
            node.ui_add_step,
            inputs=[program_id_box, t_start_box, t_stop_box, minutes_box],
            outputs=[steps_table, steps_message],
        )
        delete_step_btn.click(
            node.ui_delete_step,
            inputs=[program_id_box, delete_step_id_box],
            outputs=[steps_table, steps_message],
        )
        start_btn.click(
            node.ui_start_program,
            inputs=[program_id_box],
            outputs=[programs_message, program_run_status],
        )
        stop_btn.click(node.ui_stop_program, outputs=[programs_message, program_run_status])
        clear_meas_btn.click(node.ui_clear_measurements, inputs=[program_id_box], outputs=[clear_meas_msg])
        export_btn.click(
            node.ui_export_program,
            inputs=[program_id_box, export_limit_box, export_clear_box],
            outputs=[export_file, export_message],
        )
        save_e720_btn.click(
            node.ui_save_e720_config,
            inputs=[program_id_box, sweep_mode_box, enabled_freqs_box, range_max_box],
            outputs=[save_e720_msg],
        )
        e720_send_btn.click(
            lambda l: node.ui_e720_send_byte(command_values.get(l, 1)),
            inputs=[e720_cmd_dropdown],
            outputs=[e720_cmd_message],
        )
        e720_send_custom_btn.click(
            lambda b: node.ui_e720_send_byte(int(b)),
            inputs=[e720_custom_byte],
            outputs=[e720_cmd_message],
        )
        for (label, byte_val), btn in zip(PANEL_COMMANDS.items(), panel_btns):
            btn.click(lambda b=byte_val: node.ui_e720_send_byte(int(b)), outputs=[e720_cmd_message])

        general_timer = gr.Timer(value=node.status_refresh_period_sec)
        general_outputs = [
            ros_health_box,
            host_summary_box,
            services_table,
            disk_table,
            uart_table,
            net_overview_table,
            logs_box,
        ]
        general_timer.tick(node.ui_tick_general, outputs=general_outputs)
        demo.load(node.ui_tick_general, outputs=general_outputs)
        demo.load(node.ui_refresh_programs, outputs=[programs_table, programs_message])

    with demo.route('Experiment', '/experiment') as experiment_page:
        experiment_tick_outputs = build_experiment_page(node)
        experiment_page.load(node.ui_tick_experiment, outputs=experiment_tick_outputs)

    with demo.route('Configuration', '/configuration') as configuration_page:
        cfg_load_outputs = build_configuration_tab(node)
        configuration_page.load(node.ui_load_configuration, outputs=cfg_load_outputs)

    return demo
