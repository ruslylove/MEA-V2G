from flask import Flask, jsonify
from werkzeug.serving import make_server
import threading

class ApiServer(threading.Thread):
    def __init__(self, sim_instance, port=5000):
        super().__init__()
        self.sim_instance = sim_instance
        self.port = port
        self.daemon = True

        self.app = Flask(__name__)
        self.app.add_url_rule('/status', 'status', self.get_status)

        self.server = make_server('0.0.0.0', port, self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def run(self):
        print(f"Starting API server on http://0.0.0.0:{self.port}")
        self.server.serve_forever()

    def shutdown(self):
        print("Shutting down API server...")
        self.server.shutdown()

    def get_status(self):
        """
        Inspects the simulation instance and returns the relevant status.
        """
        from Ev import Ev
        from Evse import Evse

        status_data = {"role": "unknown"}

        if isinstance(self.sim_instance, Ev):
            battery = self.sim_instance.getBattery()
            if battery:
                status_data.update({
                    "role": "EV",
                    "soc": battery.getSOC(),
                    "level_wh": battery.getLevel(),
                    "capacity_wh": battery.getCapacity(),
                    "is_charging": battery.is_charging,
                    "in_voltage": battery.in_voltage,
                    "in_current": battery.in_current
                })

        elif isinstance(self.sim_instance, Evse):
            charger = self.sim_instance.getCharger()
            if charger:
                status_data.update({
                    "role": "EVSE",
                    "present_voltage": charger.getEvsePresentVoltage(),
                    "present_current": charger.getEvsePresentCurrent(),
                    "target_voltage": charger.ev_target_voltage,
                    "target_current": charger.ev_target_current,
                    "max_power": charger.getEvseMaxPower(),
                    "is_charging": self.sim_instance.charging
                })

        return jsonify(status_data)