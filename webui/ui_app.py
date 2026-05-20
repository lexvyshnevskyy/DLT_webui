from __future__ import annotations

from typing import TYPE_CHECKING

import gradio as gr

from webui.ui_config_tab import build_configuration_tab
from webui.ui_experiment_page import build_experiment_page
from webui.ui_programs import (
    build_program_create,
    build_program_edit,
    build_program_view,
    build_programs_list,
)

if TYPE_CHECKING:
    from webui.node import WebHMINode


def build_ui(node: 'WebHMINode') -> gr.Blocks:
    with gr.Blocks(title=node.title, theme=gr.themes.Soft()) as demo:
        gr.Navbar(main_page_name='Dashboard')
        gr.Markdown(f'# {node.title}')
        gr.Markdown('System status on this page. Use the menu for **Programs**, **Experiment**, and **Configuration**.')

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
                headers=['interface', 'state', 'mac', 'ipv4'],
                interactive=False,
                label='Network interfaces',
            )
            logs_box = gr.Textbox(label='Event log', lines=10, interactive=False)

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

    with demo.route('Programs', 'programs') as programs_page:
        programs_list_outputs = build_programs_list(node)
        programs_page.load(node.ui_programs_list_refresh, outputs=programs_list_outputs)

    with demo.route('New program', 'program-new'):
        build_program_create(node)

    with demo.route('Edit program', 'program-edit') as edit_page:
        program_edit_outputs = build_program_edit(node)
        edit_page.load(node.ui_program_edit_load, outputs=program_edit_outputs)

    with demo.route('Program details', 'program-view') as view_page:
        program_view_outputs = build_program_view(node)
        view_page.load(node.ui_program_view_load, outputs=program_view_outputs)

    with demo.route('Experiment', 'experiment') as experiment_page:
        experiment_tick_outputs = build_experiment_page(node)
        experiment_page.load(node.ui_tick_experiment, outputs=experiment_tick_outputs)

    with demo.route('Configuration', 'configuration') as configuration_page:
        cfg_load_outputs = build_configuration_tab(node)
        configuration_page.load(node.ui_load_configuration, outputs=cfg_load_outputs)

    return demo
