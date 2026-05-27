from __future__ import annotations

from typing import List

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from webui.render import template_response

router = APIRouter()


def _tpl(request: Request):
    return request.app.state.templates, request.app.state.node


@router.get('/configuration', response_class=HTMLResponse)
async def configuration_page(request: Request, iface: str = '') -> HTMLResponse:
    templates, node = _tpl(request)
    ctx = node.get_configuration_context(iface or None)
    return template_response(templates, request, 'config.html', ctx)


@router.post('/configuration/reload')
async def configuration_reload(request: Request) -> RedirectResponse:
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/network/select')
async def network_select(request: Request, iface: str = Form('eth0')) -> RedirectResponse:
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/up')
async def network_up(request: Request, iface: str = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_net_up(iface)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/down')
async def network_down(request: Request, iface: str = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_net_down(iface)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/dhcp')
async def network_dhcp(request: Request, iface: str = Form(...)) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_net_dhcp(iface)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/static')
async def network_static(
    request: Request,
    iface: str = Form(...),
    address: str = Form(...),
    prefix: float = Form(24),
    gateway: str = Form(''),
    dns: str = Form(''),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_net_apply_static(iface, address, prefix, gateway, dns)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/wifi-scan')
async def wifi_scan(request: Request, iface: str = Form('wlan0')) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_wifi_scan(iface)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/wifi-connect')
async def wifi_connect(
    request: Request,
    iface: str = Form('wlan0'),
    ssid: str = Form(...),
    password: str = Form(''),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_wifi_connect(ssid, password, iface)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/hotspot-enable')
async def hotspot_enable(request: Request, iface: str = Form('wlan0')) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_hotspot_enable(iface)
    return RedirectResponse(url=f'/configuration?iface={iface}', status_code=303)


@router.post('/configuration/network/hotspot-disable')
async def hotspot_disable(request: Request) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_hotspot_disable()
    return RedirectResponse(url='/configuration?iface=wlan0', status_code=303)


@router.post('/configuration/ltm')
async def save_ltm(
    request: Request,
    port: str = Form(...),
    baudrate: float = Form(230400),
    restart: bool = Form(False),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_save_ltm2985_config(port, baudrate, restart)
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/measure')
async def save_measure(
    request: Request,
    port: str = Form(...),
    speed: float = Form(9600),
    restart: bool = Form(False),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_save_measure_device_config(port, speed, restart)
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/database/test')
async def db_test(
    request: Request,
    host: str = Form('127.0.0.1'),
    port: float = Form(3306),
    name: str = Form('exp'),
    user: str = Form('delatometry'),
    password: str = Form(''),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_test_database_connection(host, port, name, user, password)
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/database')
async def save_db(
    request: Request,
    host: str = Form(...),
    port: float = Form(3306),
    name: str = Form(...),
    user: str = Form(...),
    password: str = Form(''),
    auto_init: bool = Form(False),
    restart: bool = Form(False),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_save_database_config(host, port, name, user, password, auto_init, restart)
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/core')
async def save_core(
    request: Request,
    pwm_ch1: str = Form('18'),
    pwm_ch2: str = Form('19'),
    enable_db_client: bool = Form(False),
    enable_pwm: bool = Form(False),
    restart: bool = Form(False),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_save_core_config(pwm_ch1, pwm_ch2, enable_db_client, enable_pwm, restart)
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/ads')
async def save_ads(
    request: Request,
    enabled: bool = Form(False),
    simulate: bool = Form(False),
    fallback: bool = Form(True),
    restart: bool = Form(False),
) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_save_ads1256_config(enabled, simulate, fallback, restart)
    return RedirectResponse(url='/configuration', status_code=303)


@router.post('/configuration/peek/ltm')
async def peek_ltm(request: Request) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_peek_ltm_topic()
    return RedirectResponse(url='/configuration#ltm', status_code=303)


@router.post('/configuration/peek/e720')
async def peek_e720(request: Request) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_peek_e720_topic()
    return RedirectResponse(url='/configuration#e720', status_code=303)


@router.post('/configuration/peek/ads')
async def peek_ads(request: Request) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_peek_ads_topic()
    return RedirectResponse(url='/configuration#ads', status_code=303)


@router.post('/configuration/peek/hmi')
async def peek_hmi(request: Request) -> RedirectResponse:
    _, node = _tpl(request)
    node.ui_peek_hmi_topic()
    return RedirectResponse(url='/configuration#hmi', status_code=303)
