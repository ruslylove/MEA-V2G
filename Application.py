import argparse
import json
from Evse import *
from Ev import *

if __name__ == "__main__":
    WHITEBBET_DEFAULT_MAC = "00:01:01:63:77:33"
    parser = argparse.ArgumentParser(description='Codico Whitebeet reference implementation.')
    parser.add_argument('interface_type', type=str, choices=('eth', 'spi'), help='Type of the interface through which the Whitebeet is connected. ("eth" or "spi").')
    parser.add_argument('-i', '--interface', type=str, required=True, help='This is the name of the interface where the Whitebeet is connected to (i.e. for eth "eth0" or spi "0").')
    parser.add_argument('-m', '--mac', type=str, help='This is the MAC address of the ethernet interface of the Whitebeet (i.e. "{}").'.format(WHITEBBET_DEFAULT_MAC))
    parser.add_argument('-r', '--role', type=str, choices=('EVSE', 'EV'), required=True, help='This is the role of the Whitebeet. "EV" for EV mode and "EVSE" for EVSE mode')
    parser.add_argument('-c', '--config', type=str, help='Path to EV configuration file. Defaults to ./ev.json.\nA MAC present in the config file will override a MAC provided with -m argument.', nargs='?', const="./ev.json")
    parser.add_argument('-ec', '--evse-config', type=str, help='Path to EVSE configuration file. Defaults to ./evse.json.\nA MAC present in the config file will override a MAC provided with -m argument.', nargs='?', const="./evse.json")
    parser.add_argument('-p', '--portmirror', help='Enables port mirror.', action='store_true')
    args = parser.parse_args()

    print(f'Welcome to Codico Whitebeet {args.role} reference implementation')

    # role is EV
    if(args.role == "EV"):
        mac = args.mac
        config = None
        # Load configuration from json
        if args.config is not None:
            try:
                with open(args.config, 'r') as configFile:
                    config = json.load(configFile)
                    if 'mac' in config and config['mac']:
                        mac = config['mac'] # Config file MAC overrides command-line
            except FileNotFoundError:
                print(f"Configuration file {args.config} not found. Using default configuration.")
            except json.JSONDecodeError:
                print(f"Error decoding {args.config}. The file is likely malformed. Using default configuration.")
                config = None # Ensure config is None if JSON is bad
                
        # If no MAC was provided by command line or config file, use the default.
        if args.interface_type == "eth" and mac is None:
            mac = WHITEBBET_DEFAULT_MAC

        if mac is None and args.interface_type == "eth":
            print("Error: A MAC address must be provided for an ethernet interface via command line (-m) or a config file (-c).")
            exit(1)

        with Ev(args.interface_type, args.interface, mac) as ev:
            # Apply config to ev
            if config is not None:
                print("EV configuration: " + str(config))
                ev.load(config)

            # Start the EVSE loop
            ev.whitebeet.networkConfigSetPortMirrorState(args.portmirror)
            ev.loop()
            print("EV loop finished")

    elif(args.role == 'EVSE'):
        evse_mac = args.mac
        evse_config_data = None
        if args.evse_config is not None:
            try:
                with open(args.evse_config, 'r') as configFile:
                    evse_config_data = json.load(configFile)
                    if 'mac' in evse_config_data:
                        evse_mac = evse_config_data['mac']
            except FileNotFoundError:
                print(f"Configuration file {args.evse_config} not found. Using default EVSE configuration.")
            except json.JSONDecodeError:
                print(f"Error decoding {args.evse_config}. The file is likely malformed. Using default EVSE configuration.")
                evse_config_data = None # Ensure config is None if JSON is bad


        with Evse(args.interface_type, args.interface, evse_mac) as evse:
            if evse_config_data and 'charger' in evse_config_data:
                charger_config = evse_config_data['charger']
                evse.getCharger().setEvseDeltaVoltage(charger_config.get('delta_voltage', 0.5))
                evse.getCharger().setEvseDeltaCurrent(charger_config.get('delta_current', 0.05))
                evse.getCharger().setEvseMaxVoltage(charger_config.get('max_voltage', 400))
                evse.getCharger().setEvseMaxCurrent(charger_config.get('max_current', 100))
                evse.getCharger().setEvseMaxPower(charger_config.get('max_power', 25000))
            else:
                # Default charger parameters
                evse.getCharger().setEvseDeltaVoltage(0.5)
                evse.getCharger().setEvseDeltaCurrent(0.05)
                evse.getCharger().setEvseMaxVoltage(400)
                evse.getCharger().setEvseMaxCurrent(100)
                evse.getCharger().setEvseMaxPower(25000)

            # Start the charger
            evse.getCharger().start()

            if evse_config_data and 'schedule' in evse_config_data:
                schedule = evse_config_data['schedule']
                evse.setSchedule(schedule)
            else:
                # Default schedule
                schedule = {
                    "code": 0,
                    "schedule_tuples": [{
                        'schedule_tuple_id': 1,
                        'schedules':[
                            {
                                "start": 0,
                                "interval": 0,
                                "power": 25000
                            },
                            {
                                "start": 1800,
                                "interval": 0,
                                "power": 18750
                            },
                            {
                                "start": 3600,
                                "interval": 82800,
                                "power": 12500
                            }
                        ]
                    }]
                }
                evse.setSchedule(schedule)

            # Start the EVSE loop
            evse.whitebeet.networkConfigSetPortMirrorState(args.portmirror)
            evse.loop()
            print("EVSE loop finished")

    print("Goodbye!")
