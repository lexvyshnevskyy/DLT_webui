from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Tuple

import gradio as gr

from webui.e720_commands import PANEL_COMMANDS, command_choices

if TYPE_CHECKING:
    from webui.node import WebHMINode


def build_ui(node: 'WebHMINode') -> gr.Blocks:
    command_labels = [label for label, _ in command_choices()]
    command_values = {label: value for label, value in command_choices()}

    with gr.Blocks(title=node.title, theme=gr.themes.Soft()) as demo:
        gr.Markdown(f'# {node.title}')
        gr.Markdown('Delatometry experiment control — auto-refresh every 1 s on live tabs.')

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
                label='UART / serial devices (from delatometry env)',
            )
            net_overview_table = gr.Dataframe(
                headers=['interface', 'state', 'mac', 'ipv4', 'ipv6', 'speed_mbps'],
                interactive=False,
                label='Network interfaces (read-only)',
            )
            logs_box = gr.Textbox(label='Event log', lines=10, interactive=False)

        with gr.Tab('Experiment'):
            experiment_banner = gr.Textbox(label='Experiment state', interactive=False)
            with gr.Row():
                with gr.Column():
                    gr.Markdown('### Temperature')
                    temp_summary = gr.Textbox(label='Temperature summary', lines=8, interactive=False)
                    measurement_table = gr.Dataframe(
                        headers=['channel', 'type', 'value', 'valid', 'age_s'],
                        interactive=False,
                        label='LTM channels',
                    )
                with gr.Column():
                    gr.Markdown('### E7-20 RCL meter')
                    e720_summary = gr.Textbox(label='E7-20 live values', lines=10, interactive=False)
                    e720_table = gr.Dataframe(
                        headers=['online', 'im', 'value1', 'sec', 'value2', 'freq', 'level', 'offset', 'range'],
                        interactive=False,
                        label='E7-20 snapshot',
                    )
            with gr.Row():
                manual_enabled_box = gr.Checkbox(label='Manual temperature control', value=False)
                manual_target_box = gr.Number(label='Manual target [K]', value=373.15)
                manual_apply_btn = gr.Button('Apply manual target')
            control_message = gr.Textbox(label='Control message', interactive=False)
            core_snapshot_box = gr.Code(label='Core control snapshot', language='json')

        with gr.Tab('Programs'):
            program_id_box = gr.Number(label='Selected program ID', precision=0, value=0)
            programs_table = gr.Dataframe(
                headers=['id', 'datetime', 'status'],
                datatype=['number', 'str', 'str'],
                interactive=False,
                label='Programs',
            )
            programs_message = gr.Textbox(label='Programs message', interactive=False)
            with gr.Row():
                create_program_btn = gr.Button('New program')
                refresh_programs_btn = gr.Button('Refresh list')
                load_program_btn = gr.Button('Load steps')
                delete_program_btn = gr.Button('Delete program', variant='stop')
            steps_table = gr.Dataframe(
                headers=['step_id', 't_start_k', 't_stop_k', 'minutes'],
                datatype=['number', 'number', 'number', 'number'],
                interactive=False,
                label='Temperature program steps',
            )
            steps_message = gr.Textbox(label='Steps message', interactive=False)
            with gr.Row():
                t_start_box = gr.Number(label='T start [K]', value=300.0)
                t_stop_box = gr.Number(label='T stop [K]', value=350.0)
                minutes_box = gr.Number(label='Minutes', value=10.0)
            with gr.Row():
                add_step_btn = gr.Button('Add step')
                delete_step_id_box = gr.Number(label='Delete step ID', precision=0, value=0)
                delete_step_btn = gr.Button('Delete step')
            with gr.Row():
                start_btn = gr.Button('Run program', variant='primary')
                stop_btn = gr.Button('Stop experiment', variant='stop')
            with gr.Row():
                export_limit_box = gr.Number(label='Export row limit', value=50000, precision=0)
                export_btn = gr.Button('Export program data (ZIP)')
            export_file = gr.File(label='Download export', interactive=False)
            export_message = gr.Textbox(label='Export message', interactive=False)

            gr.Markdown('### E7-20 controls (same byte commands as legacy Delphi)')
            with gr.Row():
                e720_cmd_dropdown = gr.Dropdown(choices=command_labels, value=command_labels[0] if command_labels else None, label='Command')
                e720_send_btn = gr.Button('Send to E7-20')
            with gr.Row():
                e720_custom_byte = gr.Number(label='Custom byte (0–255)', precision=0, value=1)
                e720_send_custom_btn = gr.Button('Send custom byte')
            e720_cmd_message = gr.Textbox(label='E7-20 command result', interactive=False)
            gr.Markdown('Quick panel keys')
            panel_btns = []
            panel_row = None
            for idx, (label, byte_val) in enumerate(PANEL_COMMANDS.items()):
                if idx % 8 == 0:
                    panel_row = gr.Row()
                with panel_row:
                    panel_btns.append(gr.Button(label))

        with gr.Tab('Network'):
            gr.Markdown(
                'Configure networking via NetworkManager (`nmcli`). '
                'Requires passwordless sudo for nmcli on the Pi — see webui README.'
            )
            net_iface_table = gr.Dataframe(
                headers=['interface', 'state', 'mac', 'ipv4', 'ipv6', 'speed_mbps'],
                interactive=False,
                label='Interfaces',
            )
            with gr.Row():
                nm_connection_box = gr.Dropdown(choices=[], label='NM connection profile')
                refresh_nm_btn = gr.Button('Refresh connections')
            gr.Markdown('### Static IPv4')
            with gr.Row():
                static_ip_box = gr.Textbox(label='Address', placeholder='192.168.1.50')
                static_prefix_box = gr.Number(label='Prefix', value=24, precision=0)
                static_gw_box = gr.Textbox(label='Gateway', placeholder='192.168.1.1')
                static_dns_box = gr.Textbox(label='DNS', placeholder='192.168.1.1')
            with gr.Row():
                apply_static_btn = gr.Button('Apply static IPv4')
                apply_dhcp_btn = gr.Button('Switch to DHCP')
            static_msg = gr.Textbox(label='IPv4 result', interactive=False)
            gr.Markdown('### Wi‑Fi')
            with gr.Row():
                wifi_ssid_box = gr.Textbox(label='SSID')
                wifi_password_box = gr.Textbox(label='Password', type='password')
                wifi_iface_box = gr.Dropdown(choices=['wlan0', 'wlan1'], value='wlan0', label='Wi‑Fi interface')
            with gr.Row():
                wifi_scan_btn = gr.Button('Scan Wi‑Fi')
                wifi_connect_btn = gr.Button('Connect', variant='primary')
            wifi_table = gr.Dataframe(
                headers=['in_use', 'ssid', 'signal', 'security'],
                interactive=False,
                label='Available networks',
            )
            wifi_msg = gr.Textbox(label='Wi‑Fi result', interactive=False)

        # --- button wiring ---
        service_start_btn.click(
            lambda u: node.ui_service_control(u, 'start'),
            inputs=[service_unit_box],
            outputs=[service_action_msg],
        )
        service_stop_btn.click(
            lambda u: node.ui_service_control(u, 'stop'),
            inputs=[service_unit_box],
            outputs=[service_action_msg],
        )
        service_restart_btn.click(
            lambda u: node.ui_service_control(u, 'restart'),
            inputs=[service_unit_box],
            outputs=[service_action_msg],
        )

        manual_apply_btn.click(
            node.ui_manual_target,
            inputs=[manual_target_box, manual_enabled_box],
            outputs=[core_snapshot_box],
        )
        refresh_programs_btn.click(node.ui_refresh_programs, outputs=[programs_table, programs_message])
        create_program_btn.click(node.ui_create_program, outputs=[program_id_box, programs_table, programs_message])
        load_program_btn.click(node.ui_load_program, inputs=[program_id_box], outputs=[steps_table, steps_message])
        delete_program_btn.click(node.ui_delete_program, inputs=[program_id_box], outputs=[programs_table, programs_message, program_id_box])
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
        start_btn.click(node.ui_start_program, inputs=[program_id_box], outputs=[control_message, experiment_banner])
        stop_btn.click(node.ui_stop_program, outputs=[control_message, experiment_banner])
        export_btn.click(
            node.ui_export_program,
            inputs=[program_id_box, export_limit_box],
            outputs=[export_file, export_message],
        )

        def _send_named(cmd_label: str) -> str:
            byte_val = command_values.get(cmd_label, 1)
            return node.ui_e720_send_byte(int(byte_val))

        e720_send_btn.click(_send_named, inputs=[e720_cmd_dropdown], outputs=[e720_cmd_message])
        e720_send_custom_btn.click(
            lambda b: node.ui_e720_send_byte(int(b)),
            inputs=[e720_custom_byte],
            outputs=[e720_cmd_message],
        )
        for (label, byte_val), btn in zip(PANEL_COMMANDS.items(), panel_btns):
            btn.click(lambda b=byte_val: node.ui_e720_send_byte(int(b)), outputs=[e720_cmd_message])

        refresh_nm_btn.click(node.ui_refresh_nm_connections, outputs=[nm_connection_box])
        apply_static_btn.click(
            node.ui_apply_static_ip,
            inputs=[nm_connection_box, static_ip_box, static_prefix_box, static_gw_box, static_dns_box],
            outputs=[static_msg],
        )
        apply_dhcp_btn.click(node.ui_apply_dhcp, inputs=[nm_connection_box], outputs=[static_msg])
        wifi_scan_btn.click(node.ui_wifi_scan, outputs=[wifi_table, wifi_msg])
        wifi_connect_btn.click(
            node.ui_wifi_connect,
            inputs=[wifi_ssid_box, wifi_password_box, wifi_iface_box],
            outputs=[wifi_msg],
        )

        def _tick_general() -> Tuple[Any, ...]:
            return node.ui_tick_general()

        def _tick_experiment() -> Tuple[Any, ...]:
            return node.ui_tick_experiment()

        def _tick_network() -> Tuple[Any, ...]:
            return node.ui_tick_network()

        timer = gr.Timer(value=node.status_refresh_period_sec)
        timer.tick(
            _tick_general,
            outputs=[
                ros_health_box,
                host_summary_box,
                services_table,
                disk_table,
                uart_table,
                net_overview_table,
                logs_box,
            ],
        )
        timer.tick(
            _tick_experiment,
            outputs=[
                experiment_banner,
                temp_summary,
                measurement_table,
                e720_summary,
                e720_table,
                core_snapshot_box,
            ],
        )
        timer.tick(_tick_network, outputs=[net_iface_table])

        demo.load(_tick_general, outputs=[ros_health_box, host_summary_box, services_table, disk_table, uart_table, net_overview_table, logs_box])
        demo.load(_tick_experiment, outputs=[experiment_banner, temp_summary, measurement_table, e720_summary, e720_table, core_snapshot_box])
        demo.load(node.ui_refresh_programs, outputs=[programs_table, programs_message])
        demo.load(node.ui_refresh_nm_connections, outputs=[nm_connection_box])

    return demo
