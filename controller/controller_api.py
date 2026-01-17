"""Controller API: runs the control manager and exposes a minimal HTTP endpoint for snapshots."""

from __future__ import annotations

import os
import cherrypy
import logging

from controller.cu_instancer import get_manager
from controller import state_memory as sm
from logging_setup import configure_logging

configure_logging()
logger = logging.getLogger("controller_api")


def _ts():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ControllerAPI:
    exposed = True

    def __init__(self):
        self.manager = get_manager()
        logger.info("Controller API started.")

    @cherrypy.tools.json_out()
    def GET(self, *uri, **_params):
        if not uri:
            return {"ok": True, "endpoints": ["/health", "/snapshot"]}
        path = uri[0].lower()
        if path == "health":
            return {"ok": True, "ts": _ts()}
        if path == "snapshot":
            return sm.get_snapshot()
        return {"error": "invalid endpoint"}


def run():
    cherrypy.config.update({"server.socket_host": "0.0.0.0", "server.socket_port": 8081})
    conf = {"/": {"request.dispatch": cherrypy.dispatch.MethodDispatcher()}}
    cherrypy.tree.mount(ControllerAPI(), "/", conf)
    cherrypy.engine.start()
    cherrypy.engine.block()


if __name__ == "__main__":
    run()
