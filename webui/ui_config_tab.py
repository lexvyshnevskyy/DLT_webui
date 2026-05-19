from __future__ import annotations

from typing import TYPE_CHECKING, List

import gradio as gr

if TYPE_CHECKING:
    from webui.node import WebHMINode


def _topic_test_row(node: 'WebHMINode', label: str, handler_name: str) -> gr.Textbox:
    topic_out = gr.Textbox(label=f'{label} topic data', lines=6, interactive=False)
    with gr.Row():
        gr.Button(f'Read {label} topic', variant='secondary').click(
            getattr(node, handler_name),
            outputs=topic_out,
        )
    return topic_out


def build_configuration_tab(node: 'WebHMINode') -> List[gr.components.Component]:
    gr.Markdown(
        'System configuration is stored in `/etc/default/delatometry`. '
        'Save applies changes; enable **Restart service** to apply immediately.'
    )
    cfg_refresh_btn = gr.Button('Refresh all settings', variant='secondary')
    cfg_status = gr.Textbox(label='Configuration status', interactive=False)

    with gr.Accordion('Network', open=False):
        gr.Markdown('NetworkManager (`nmcli`) — requires sudoers for IP/Wi‑Fi changes.')
        net_iface_table = gr.Dataframe(
            headers=['interface', 'state', 'mac', 'ipv4', 'ipv6', 'speed_mbps'],
            interactive=False,
            label='Interfaces',
        )
        net_refresh_btn = gr.Button('Refresh interfaces')
        with gr.Row():
            nm_connection_box = gr.Dropdown(choices=[], label='Connection profile')
            refresh_nm_btn = gr.Button('Refresh profiles')
        gr.Markdown('#### Static IPv4')
        with gr.Row():
            static_ip_box = gr.Textbox(label='Address', placeholder='192.168.1.50')
            static_prefix_box = gr.Number(label='Prefix', value=24, precision=0)
            static_gw_box = gr.Textbox(label='Gateway', placeholder='192.168.1.1')
            static_dns_box = gr.Textbox(label='DNS', placeholder='192.168.1.1')
        with gr.Row():
            apply_static_btn = gr.Button('Apply static IPv4')
            apply_dhcp_btn = gr.Button('Use DHCP')
        static_msg = gr.Textbox(label='IPv4 result', interactive=False)
        gr.Markdown('#### Wi‑Fi')
        with gr.Row():
            wifi_ssid_box = gr.Textbox(label='SSID')
            wifi_password_box = gr.Textbox(label='Password', type='password')
            wifi_iface_box = gr.Dropdown(choices=['wlan0', 'wlan1'], value='wlan0', label='Wi‑Fi interface')
        with gr.Row():
            wifi_scan_btn = gr.Button('Scan')
            wifi_connect_btn = gr.Button('Connect', variant='primary')
        wifi_table = gr.Dataframe(
            headers=['in_use', 'ssid', 'signal', 'security'],
            interactive=False,
            label='Networks',
        )
        wifi_msg = gr.Textbox(label='Wi‑Fi result', interactive=False)

    with gr.Accordion('LTM2985 UART', open=True):
        ltm_topic_out = _topic_test_row(node, 'LTM2985', 'ui_peek_ltm_topic')
        with gr.Row():
            ltm_port = gr.Dropdown(label='Serial port', choices=[], allow_custom_value=True)
            ltm_baud = gr.Number(label='Baud rate', precision=0, value=230400)
        with gr.Row():
            ltm_restart = gr.Checkbox(label='Restart service after save', value=True)
            ltm_save = gr.Button('Save LTM2985 settings', variant='primary')
        ltm_msg = gr.Textbox(label='Result', interactive=False)

    with gr.Accordion('E7-20 / measure_device', open=False):
        e720_topic_out = _topic_test_row(node, 'E7-20', 'ui_peek_e720_topic')
        with gr.Row():
            meas_port = gr.Dropdown(label='Serial port', choices=[], allow_custom_value=True)
            meas_speed = gr.Number(label='Baud rate', precision=0, value=9600)
        with gr.Row():
            meas_restart = gr.Checkbox(label='Restart service after save', value=True)
            meas_save = gr.Button('Save measure_device settings', variant='primary')
        meas_msg = gr.Textbox(label='Result', interactive=False)

    with gr.Accordion('HMI (Nextion)', open=False):
        gr.Markdown('UART is fixed on-board; test reads subscribed ROS topics used by HMI.')
        hmi_topic_out = _topic_test_row(node, 'HMI inputs', 'ui_peek_hmi_topic')

    with gr.Accordion('Database (MariaDB)', open=False):
        with gr.Row():
            db_host = gr.Textbox(label='Host', value='127.0.0.1')
            db_port = gr.Number(label='Port', precision=0, value=3306)
        with gr.Row():
            db_name = gr.Textbox(label='Database name', value='exp')
            db_user = gr.Textbox(label='User', value='delatometry')
        db_password = gr.Textbox(label='Password', type='password')
        db_auto_init = gr.Checkbox(label='Auto-init schema', value=True)
        db_test_btn = gr.Button('Test connection', variant='secondary')
        db_test_msg = gr.Textbox(label='Connection test', interactive=False)
        with gr.Row():
            db_restart = gr.Checkbox(label='Restart service after save', value=True)
            db_save = gr.Button('Save database settings', variant='primary')
        db_msg = gr.Textbox(label='Result', interactive=False)

    with gr.Accordion('Core (temperature control)', open=False):
        with gr.Row():
            core_pwm_ch1 = gr.Dropdown(label='PWM CH1 GPIO (BCM)', choices=[], value='18')
            core_pwm_ch2 = gr.Dropdown(label='PWM CH2 GPIO (BCM)', choices=[], value='19')
        with gr.Row():
            core_db_client = gr.Checkbox(label='Enable database client', value=False)
            core_pwm = gr.Checkbox(label='Enable PWM / heater control', value=False)
        with gr.Row():
            core_restart = gr.Checkbox(label='Restart service after save', value=True)
            core_save = gr.Button('Save core settings', variant='primary')
        core_msg = gr.Textbox(label='Result', interactive=False)

    with gr.Accordion('ADS1256', open=False):
        ads_topic_out = _topic_test_row(node, 'ADS1256', 'ui_peek_ads_topic')
        ads_enabled = gr.Checkbox(label='Enable ADS1256 node (systemd)', value=False)
        ads_simulate = gr.Checkbox(label='Simulate (no hardware)', value=False)
        ads_fallback = gr.Checkbox(label='Fallback to simulation on error', value=True)
        with gr.Row():
            ads_restart = gr.Checkbox(label='Restart service after save', value=True)
            ads_save = gr.Button('Save ADS1256 settings', variant='primary')
        ads_msg = gr.Textbox(label='Result', interactive=False)

    load_outputs = [
        cfg_status,
        net_iface_table,
        nm_connection_box,
        ltm_port,
        ltm_baud,
        meas_port,
        meas_speed,
        db_host,
        db_port,
        db_name,
        db_user,
        db_password,
        db_auto_init,
        core_pwm_ch1,
        core_pwm_ch2,
        core_db_client,
        core_pwm,
        ads_enabled,
        ads_simulate,
        ads_fallback,
    ]

    cfg_refresh_btn.click(node.ui_load_configuration, outputs=load_outputs)
    net_refresh_btn.click(node.ui_tick_network, outputs=[net_iface_table])
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
    ltm_save.click(
        node.ui_save_ltm2985_config,
        inputs=[ltm_port, ltm_baud, ltm_restart],
        outputs=[ltm_msg, cfg_status],
    )
    meas_save.click(
        node.ui_save_measure_device_config,
        inputs=[meas_port, meas_speed, meas_restart],
        outputs=[meas_msg, cfg_status],
    )
    db_test_btn.click(
        node.ui_test_database_connection,
        inputs=[db_host, db_port, db_name, db_user, db_password],
        outputs=[db_test_msg],
    )
    db_save.click(
        node.ui_save_database_config,
        inputs=[db_host, db_port, db_name, db_user, db_password, db_auto_init, db_restart],
        outputs=[db_msg, cfg_status],
    )
    core_save.click(
        node.ui_save_core_config,
        inputs=[core_pwm_ch1, core_pwm_ch2, core_db_client, core_pwm, core_restart],
        outputs=[core_msg, cfg_status],
    )
    ads_save.click(
        node.ui_save_ads1256_config,
        inputs=[ads_enabled, ads_simulate, ads_fallback, ads_restart],
        outputs=[ads_msg, cfg_status],
    )

    return load_outputs
