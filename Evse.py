import time
from Whitebeet import *
from Charger import *
from api_server import ApiServer

class Evse():
    def __init__(self, iftype, iface, mac, auto_authorize=False, api_port=None):
        self.whitebeet = Whitebeet(iftype, iface, mac)
        print(f"WHITE-beet-EI firmware version: {self.whitebeet.version}")
        self.charger = Charger()
        self.schedule = None
        self.evse_config = None
        self.auto_authorize = auto_authorize
        self.charging = False
        self.state = "Unavailable" # Initial state
        self.state_timer = 0
        self.SUSPENDED_TIMEOUT = 300 # 5 minutes
        self.api_server = None
        self.ocpp_worker = None
        self.authorized_id_tag = None
        self.reservation_expiry_time = None

        if api_port:
            self.api_server = ApiServer(self, port=api_port)
            self.api_server.start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.api_server:
            self.api_server.shutdown()
        if hasattr(self, "whitebeet"):
            del self.whitebeet

    def __del__(self):
        if self.api_server:
            self.api_server.shutdown()
        if hasattr(self, "whitebeet"):
            del self.whitebeet

    def _initialize(self):
        """
        Initializes the whitebeet by setting the control pilot mode and setting the duty cycle
        to 100%. The SLAC module is also started. This one needs ~1s to 2s to be ready.
        Therefore we delay the initialization by 2s.
        """

        print("Set the CP mode to EVSE")
        self.set_status("Available")
        self.whitebeet.controlPilotSetMode(1)
        print("Set the CP duty cycle to 100%")
        self.whitebeet.controlPilotSetDutyCycle(100)
        print("Start the CP service")
        self.whitebeet.controlPilotStart()
        print("Start SLAC in EVSE mode")
        self.whitebeet.slacStart(1)
        time.sleep(2)

    def _waitEvConnected(self, timeout):
        """
        We check for the state on the CP. When there is no EV connected we have state A on CP.
        When an EV connects the state changes to state B and we can continue with further steps.
        """
        timestamp_start = time.time()
        cp_state = self.whitebeet.controlPilotGetState()
                elif cp_state == 1:
                    print("EV already connected")
                    # If we booted up and car is connected, we might assume preparing?
                    # Or stay unavailable until ocpp boot?
                    # For now, if we are unavailable logic, we don't switch.
                    if self.state in ["Available", "Reserved"]:
                        self.set_status("Preparing")
                    return True
                elif cp_state > 1:
            print("CP in wrong state: {}".format(cp_state))
            return False
        else:
            print("Wait until an EV connects")
            while True:
                cp_state = self.whitebeet.controlPilotGetState()
                if timeout != None and timestamp_start + timeout > time.time():
                    return False
                if cp_state == 0:
                    time.sleep(0.1)
                elif cp_state == 1:
                    print("EV connected")
                    # Only transition if logically allowed
                    if self.state in ["Available", "Reserved"]:
                        self.set_status("Preparing")
                    else:
                        print(f"EV Connected but state is {self.state}, not transitioning to Preparing.")
                    return True
                else:
                    print("CP in wrong state: {}".format(cp_state))
                    return False

    def _handleEvConnected(self):
        """
        When an EV connected we start our start matching process of the SLAC which will be ready
        to answer SLAC request for the next 50s. After that we set the duty cycle on the CP to 5%
        which indicates that the EV can start with sending SLAC requests.
        """
        print("Start SLAC matching")
        self.whitebeet.slacStartMatching()
        print("Set duty cycle to 5%")
        self.whitebeet.controlPilotSetDutyCycle(5)
        try:
            if self.whitebeet.slacMatched() == True:
                print("SLAC matching successful")
                self._handleNetworkEstablished()
                return True
            else:
                print("SLAC matching failed")
                return False
        except TimeoutError as e:
            print(e)
            return False

    def _handleNetworkEstablished(self):
        """
        When SLAC was successful we can start the V2G module. Set our supported protocols,
        available payment options and energy transfer modes. Then we start waiting for
        notifications for requested parameters.
        """
        print("Set V2G mode to EVSE")
        self.whitebeet.v2gSetMode(1)

        self.evse_config = {
            "evse_id_DIN": '+49*123*456*789',
            "evse_id_ISO": 'DE*A23*E45B*78C',
            "protocol": [0, 1], 
            "payment_method": [0],
            "energy_transfer_mode": [0, 1, 2, 3, 4, 5],
            "certificate_installation_support": False,
            "certificate_update_support": False,
        }
        self.whitebeet.v2gEvseSetConfiguration(self.evse_config)

        self.dc_charging_parameters = {
            'isolation_level': 0,
            'min_voltage': self.charger.getEvseMinVoltage(),
            'min_current': self.charger.getEvseMinCurrent(),
            'max_voltage': self.charger.getEvseMaxVoltage(),
            'max_current': self.charger.getEvseMaxCurrent(),
            'max_power': self.charger.getEvseMaxPower(),
            'peak_current_ripple': int(self.charger.getEvseDeltaCurrent()),
            'status': 0
        }
        self.whitebeet.v2gEvseSetDcChargingParameters(self.dc_charging_parameters)

        self.ac_charging_parameters = {
            'rcd_status': 0,
            'nominal_voltage': self.charger.getEvseMaxVoltage(),
            'max_current': self.charger.getEvseMaxCurrent(),
        }
        self.whitebeet.v2gEvseSetAcChargingParameters(self.ac_charging_parameters)

        time.sleep(0.1)
        print("Start V2G")
        self.whitebeet.v2gEvseStartListen()
        while True:
            if self.charging:
                # Continuously update the charger's simulated output
                self.charger.getEvsePresentVoltage()
                self.charger.getEvsePresentCurrent()

                id, data = self.whitebeet.v2gEvseReceiveRequestSilent()

                charging_parameters = {
                    'isolation_level': 0,
                    'present_voltage': int(self.charger.getEvsePresentVoltage()),
                    'present_current': int(self.charger.getEvsePresentCurrent()),
                    'max_voltage': int(self.charger.getEvseMaxVoltage()),
                    'max_current': int(self.charger.getEvseMaxCurrent()),
                    'max_power': int(self.charger.getEvseMaxPower()),
                    'status': 0,
                }

                try:
                    self.whitebeet.v2gEvseUpdateDcChargingParameters(charging_parameters)
                except Warning as e:
                    print("Warning: {}".format(e))
                except ConnectionError as e:
                    print("ConnectionError: {}".format(e))
            else:
                id, data = self.whitebeet.v2gEvseReceiveRequest()
            
            # Check Timeouts
            if self.state in ["SuspendedEV", "SuspendedEVSE"]:
                if time.time() - self.state_timer > self.SUSPENDED_TIMEOUT:
                     print(f"Timeout checking in state {self.state}. Stopping session.")
                     if self.ocpp_worker:
                         self.ocpp_worker.send_stop_transaction_threadsafe(1, reason="TimeLimitReached")
                     try:
                         self.whitebeet.v2gEvseStopCharging()
                     except:
                         pass
                     self.set_status("Finishing")
                     break
            
            # Check Reservation Expiry
            if self.state == "Reserved" and self.reservation_expiry_time:
                 if time.time() > self.reservation_expiry_time:
                      print("Reservation expired locally. Returning to Available.")
                      self.set_status("Available")
                      # Clean up OcppInterface reservation? 
                      # Ideally OcppInterface should also govern this, but local strict check is good.
                      # Sync back if needed, but StatusNotification "Available" should trigger backend to clear it.

            if id == None or data == None:
                pass
            elif id == 0x80:
                self._handleSessionStarted(data)
            elif id == 0x81:
                self._handlePaymentSelected(data)
            elif id == 0x82:
                self._handleRequestAuthorization(data)
            elif id == 0x83:
                self._handleEnergyTransferModeSelected(data)
            elif id == 0x84:
                self._handleRequestSchedules(data)
            elif id == 0x85:
                self._handleDCChargeParametersChanged(data)
            elif id == 0x86:
                self._handleACChargeParametersChanged(data)
            elif id == 0x87:
                self._handleRequestCableCheck(data)
            elif id == 0x88:
                self._handlePreChargeStarted(data)
            elif id == 0x89:
                self._handleRequestStartCharging(data)
            elif id == 0x8A:
                self._handleRequestStopCharging(data)
            elif id == 0x8B:
                self._handleWeldingDetectionStarted(data)
            elif id == 0x8C:
                self._handleSessionStopped(data)
                break
            elif id == 0x8D:
                pass
            elif id == 0x8E:
                self._handleSessionError(data)
            elif id == 0x8F:
                self._handleCertificateInstallationRequested(data)
            elif id == 0x90:
                self._handleCertificateUpdateRequested(data)
            elif id == 0x91:
                self._handleMeteringReceiptStatus(data)
            else:
                print("Message ID not supported: {:02x}".format(id))
                break
        self.whitebeet.v2gEvseStopListen()

    def _handleSessionStarted(self, data):
        """
        Handle the SessionStarted notification
        """
        print("\"Session started\" received")
        message = self.whitebeet.v2gEvseParseSessionStarted(data)
        print("Protocol: {}".format(message['protocol']))
        print("Session ID: {}".format(message['session_id'].hex()))
        print("EVCC ID: {}".format(message['evcc_id'].hex()))

    def _handlePaymentSelected(self, data):
        """
        Handle the PaymentSelected notification
        """
        print("\"Payment selcted\" received")
        message = self.whitebeet.v2gEvseParsePaymentSelected(data)
        print("Selected payment method: {}".format(message['selected_payment_method']))
        if message['selected_payment_method'] == 1:
            print("Contract certificate: {}".format(message['contract_certificate'].hex()))
            print("mo_sub_ca1: {}".format(message['mo_sub_ca1'].hex()))
            print("mo_sub_ca2: {}".format(message['mo_sub_ca2'].hex()))
            print("EMAID: {}".format(message['emaid'].hex()))

    def _handleRequestAuthorization(self, data):
        """
        Handle the RequestAuthorization notification.
        The authorization status will be requested from the user.
        """
        print("\"Request Authorization\" received")
        message = self.whitebeet.v2gEvseParseAuthorizationStatusRequested(data)
        print(message['timeout'])

        if self.auto_authorize:
            print("Vehicle was authorized automatically via --auto flag!")
            try:
                self.whitebeet.v2gEvseSetAuthorizationStatus(True)
            except Warning as e:
                print("Warning: {}".format(e))
            except ConnectionError as e:
                print("ConnectionError: {}".format(e))
            return

        if self.authorized_id_tag:
            print(f"Vehicle was authorized via OCPP: {self.authorized_id_tag}")
            try:
                self.whitebeet.v2gEvseSetAuthorizationStatus(True)
                # Initiate StartTransaction
                if self.ocpp_worker:
                    self.ocpp_worker.send_start_transaction_threadsafe(1, self.authorized_id_tag)
            except Warning as e:
                print("Warning: {}".format(e))
            except ConnectionError as e:
                print("ConnectionError: {}".format(e))
            return

        # Check if RESERVED for a different tag
        if self.state == "Reserved":
             # We should check if idTag matches logic in 1.6
             # But here we don't have the tag from AuthorizationReq yet? 
             # Wait, AuthorizationStatusRequested gives us no tag in ISO 15118 PnC usually, 
             # but for EIM we might get it? 
             # Whitebeet msg doesn't seem to pass tag here easily in this parse?
             # Assuming we rely on backend/OCPP to reject if not matching.
             pass

        timeout = int(message['timeout'] / 1000) - 1
        # Promt for authorization status
        auth_str = input("Authorize the vehicle? Type \"yes\" or \"no\" in the next {}s: ".format(timeout))
        authorized = auth_str is not None and auth_str.lower() == "yes"

        print(f"Vehicle was {'authorized' if authorized else 'NOT authorized'} by user!")
        try:
            self.whitebeet.v2gEvseSetAuthorizationStatus(authorized)
            if authorized and self.ocpp_worker:
                 self.ocpp_worker.send_start_transaction_threadsafe(1, "RFID_TAG_LOCAL")
        except (Warning, ConnectionError) as e:
            print(f"{type(e).__name__}: {e}")

    def _handleEnergyTransferModeSelected(self, data):
        """
        Handle the energy transfer mode selected notification
        """
        print("\"Energy transfer mode selected\" received")
        self.charging = True
        self.set_status("Charging")
        message = self.whitebeet.v2gEvseParseEnergyTransferModeSelected(data)

        if 'departure_time' in message:        
            print('Departure time: {}'.format(message['departure_time']))

        if 'energy_request' in message:
            print('Energy request: {}'.format(message['energy_request']))

        print('Maximum voltage: {}'.format(message['max_voltage']))
        self.charger.setEvMaxVoltage(message['max_voltage'])

        if 'min_current' in message:
            print('Minimum current: {}'.format(message['min_current']))
            self.charger.setEvMinCurrent(message['min_current'])

        print('Maximum current: {}'.format(message['max_current']))
        self.charger.setEvMaxCurrent(message['max_current'])

        if 'max_power' in message:
            print('Maximum power: {}'.format(message['max_power']))
            self.charger.setEvMaxPower(message['max_power'])

        if 'energy_capacity' in message:
            print('Energy Capacity: {}'.format(message['energy_capacity']))

        if 'full_soc' in message:
            print('Full SoC: {}'.format(message['full_soc']))

        if 'bulk_soc' in message:
            print('Bulk SoC: {}'.format(message['bulk_soc']))

        if 'ready' in message:
            print('Ready: {}'.format('yes' if message['ready'] else 'no'))

        if 'error_code' in message:
            print('Error code: {}'.format(message['error_code']))

        if 'soc' in message:
            print('SoC: {}'.format(message['soc']))

        if 'selected_energy_transfer_mode' in message:
            print('Selected energy transfer mode: {}'.format(message['selected_energy_transfer_mode']))
            if not message['selected_energy_transfer_mode'] in self.evse_config['energy_transfer_mode']:
                print('Energy transfer mode mismatch!')
                try:
                    self.whitebeet.v2gEvseStopCharging()
                except Warning as e:
                    print("Warning: {}".format(e))
                except ConnectionError as e:
                    print("ConnectionError: {}".format(e))

    def _handleRequestSchedules(self, data):
        """
        Handle the RequestSchedules notification
        """
        print("\"Request Schedules\" received")
        message = self.whitebeet.v2gEvseParseSchedulesRequested(data)
        print("Max entries: {}".format(message['max_entries']))
        maxEntry = max([len(self.schedule), message['max_entries']])
        print("Set the schedule: {}".format(self.schedule))
        try:
            self.whitebeet.v2gEvseSetSchedules(self.schedule)
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))

    def _handleDCChargeParametersChanged(self, data):
        """
        Handle the DCChargeParametersChanged notification
        """
        print("\"DC Charge Parameters Changed\" received")
        message = self.whitebeet.v2gEvseParseDCChargeParametersChanged(data)

        print("EV maximum current: {}A".format(message['max_current']))
        self.charger.setEvMaxCurrent(message['max_current'])

        print("EV maximum voltage: {}V".format(message['max_voltage']))
        self.charger.setEvMaxVoltage(message['max_voltage'])

        if 'max_power' in message:
            print("EV maximum power: {}W".format(message['max_power']))
            self.charger.setEvMaxPower(message['max_power'])

        print('EV ready: {}'.format(message['ready']))
        print('Error code: {}'.format(message['error_code']))
        print("SOC: {}%".format(message['soc']))

        if 'target_voltage' in message:
            print("EV target voltage: {}V".format(message['target_voltage']))
            self.charger.setEvTargetVoltage(message['target_voltage'])

        if 'target_current' in message:
            print("EV target current: {}A".format(message['target_current']))
            self.charger.setEvTargetCurrent(message['target_current'])
        
        if 'charging_complete' in message:
            print("Charging complete: {}".format(message['charging_complete']))
        if 'bulk_charging_complete' in message:
            print("Bulk charging complete: {}".format(message['bulk_charging_complete']))
        if 'remaining_time_to_full_soc' in message:
            print("Remaining time to full SOC: {}s".format(message['remaining_time_to_full_soc']))
        if 'remaining_time_to_bulk_soc' in message:
            print("Remaining time to bulk SOC: {}s".format(message['remaining_time_to_bulk_soc']))
        
        # Check for Suspended states
        # SuspendedEV: EV is connected but not drawing current (target_current = 0)
        # SuspendedEVSE: EVSE is preventing charge (max_current = 0) - though we usually set that.
        
        target_current = message.get('target_current', 0)
        # We need to track if we were charging.
        
        if self.state == "Charging" and target_current == 0:
             self.set_status("SuspendedEV")
        elif self.state == "SuspendedEV" and target_current > 0:
             self.set_status("Charging")
        
        # Note: SuspendedEVSE logic depends on our local limits. 
        # If we set limit to 0 (Smart Charging), we should transition.
        # But here we are reacting to EV param changes.
    
        charging_parameters = {
            'isolation_level': 0,
            'present_voltage': int(self.charger.getEvsePresentVoltage()),
            'present_current': int(self.charger.getEvsePresentCurrent()),
            'max_voltage': int(self.charger.getEvseMaxVoltage()),
            'max_current': int(self.charger.getEvseMaxCurrent()),
            'max_power': int(self.charger.getEvseMaxPower()),
            'status': 0,
        }

        try:
            self.whitebeet.v2gEvseUpdateDcChargingParameters(charging_parameters)
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))

    def _handleACChargeParametersChanged(self, data):
        """
        Handle the ACChargeParametersChanged notification
        """
        print("\"AC Charge Parameters Changed\" received")
        message = self.whitebeet.v2gEvseParseACChargeParametersChanged(data)

        print("EV maximum voltage: {}V".format(message['max_voltage']))
        self.charger.setEvMaxVoltage(message['max_voltage'])

        print("EV minimum current: {}W".format(message['min_current']))
        self.charger.setEvMinCurrent(message['min_current'])

        print("EV maximum current: {}A".format(message['max_current']))
        self.charger.setEvMaxCurrent(message['max_current'])

        print("Energy amount: {}A".format(message['energy_amount']))    

        charging_parameters = {
            'rcd_status': 0,
            'max_current': int(self.charger.getEvseMaxCurrent()),
        }

        try:
            self.whitebeet.v2gEvseUpdateAcChargingParameters(charging_parameters)
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))

    def _handleRequestCableCheck(self, data):
        """
        Handle the RequestCableCheck notification
        """
        print("\"Request Cable Check Status\" received")
        self.whitebeet.v2gEvseParseCableCheckRequested(data)
        try:
            self.whitebeet.v2gEvseSetCableCheckFinished(True)
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))

    def _handlePreChargeStarted(self, data):
        """
        Handle the PreChargeStarted notification
        """
        print("\"Pre Charge Started\" received")
        self.whitebeet.v2gEvseParsePreChargeStarted(data)
        self.charger.start()

    def _handleRequestStartCharging(self, data):
        """
        Handle the StartChargingRequested notification
        """
        print("\"Start Charging Requested\" received")
        message = self.whitebeet.v2gEvseParseStartChargingRequested(data)
        print("Schedule tuple ID: {}".format(message['schedule_tuple_id']))
        print("Charging profiles: {}".format(message['charging_profiles']))
        self.charger.start()
        try:
            self.whitebeet.v2gEvseStartCharging()
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))

    def _handleRequestStopCharging(self, data):
        """
        Handle the RequestStopCharging notification
        """
        print("\"Request Stop Charging\" received")
        message = self.whitebeet.v2gEvseParseStopChargingRequested(data)
        print('Timeout: {}'.format(message['timeout']))
        print('Timeout: {}'.format('yes' if message['renegotiation'] else 'no'))
        self.charger.stop()
        try:
            self.whitebeet.v2gEvseStopCharging()
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))

    def _handleWeldingDetectionStarted(self, data):
        """
        Handle the WeldingDetectionStarted notification
        """
        print("\"Welding Detection Started\" received")
        self.whitebeet.v2gEvseParseWeldingDetectionStarted(data)

    def _handleSessionStopped(self, data):
        """
        Handle the SessionStopped notification
        """
        self.charging = False
        self.authorized_id_tag = None # Reset Auth
        print("\"Session stopped\" received")
        if self.ocpp_worker:
            self.ocpp_worker.send_stop_transaction_threadsafe(1, reason="Local")
        self.set_status("Finishing")
        message = self.whitebeet.v2gEvseParseSessionStopped(data)
        print('Closure type: {}'.format(message['closure_type']))
        self.charger.stop()

    def _handleSessionError(self, data):
        """
        Handle the SessionError notification
        """
        print("\"Session Error\" received")
        self.charging = False
        self.authorized_id_tag = None
        if self.ocpp_worker:
            self.ocpp_worker.send_stop_transaction_threadsafe(1, reason="Error")
        self.set_status("Faulted", error_code="OtherError")
            
        message = self.whitebeet.v2gEvseParseSessionError(data)
        self.charger.stop()

        error_messages = {
            0: 'Unspecified',
            1: 'Sequence error',
            2: 'Service ID invalid',
            3: 'Unknown session',
            4: 'Service selection invalid',
            5: 'Payment selection invalid',
            6: 'Certificate expired',
            7: 'Signature Error',
            8: 'No certificate available',
            9: 'Certificate chain error',
            10: 'Challenge invalid',
            11: 'Contract canceled',
            12: 'Wrong charge parameter',
            13: 'Power delivery not applied',
            14: 'Tariff selection invalid',
            15: 'Charging profile invalid',
            16: 'Present voltage too low',
            17: 'Metering signature not valid',
            18: 'No charge service selected',
            19: 'Wrong energy transfer type',
            20: 'Contactor error',
            21: 'Certificate not allowed at this EVSE',
            22: 'Certificate revoked',
            23: 'Charge parameter timeout reached'
        }

        print('Session error: {}: {}'.format(message['error_code'], error_messages[message['error_code']]))
        try:
            self.whitebeet.v2gEvseStopCharging()
        except Warning as e:
            print("Warning: {}".format(e))
            self.whitebeet.v2gEvseStopListen()
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))
            self.whitebeet.v2gEvseStopListen()

    def _handleCertificateInstallationRequested(self, data):
        """
        Handle the CertificateInstallationRequested notification
        """
        print("\"Certificate Installation Requested\" received")
        message = self.whitebeet.v2gEvseParseCertificateInstallationRequested(data)
        print('Timeout: {}'.format(message['timeout']))
        print('EXI request: {}'.format(message['exi_request']))

        status = 2
        certificationResponse = []

        '''startTime = time.time_ns() / 1000
        
        if self.certificateApi.isRunning:
            try:
                certificationResponse = self.certificateApi.generateResponse(message['exi_request'])
                currentTime = time.time_ns() / 1000
                status = 0
            except (Exception, KeyboardInterrupt):
                self.certificateApi.terminateAllProcesses()
        
        if currentTime > (startTime + message['timeout']):
            status = 1
            certificationResponse = []

        try:
            self.whitebeet.v2gEvseSetCertificateInstallationAndUpdateResponse(status, certificationResponse)
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))'''

    def _handleCertificateUpdateRequested(self, data):
        """
        Handle the CertificateUpdateRequested notification
        """
        print("\"Certificate Update Requested\" received")
        message = self.whitebeet.v2gEvseParseCertificateUpdateRequested(data)
        print('Timeout: {}'.format(message['timeout']))
        print('EXI request: {}'.format(message['exi_request']))

        status = 2
        certificationResponse = []

        '''startTime = time.time_ns() / 1000
        
        if self.certificateApi.isRunning:
            try:
                certificationResponse = self.certificateApi.generateResponse(message['exi_request'])
                currentTime = time.time_ns() / 1000
                status = 0
            except (Exception, KeyboardInterrupt):
                self.certificateApi.terminateAllProcesses()
        
        if currentTime > (startTime + message['timeout']):
            status = 1
            certificationResponse = []

        try:
            self.whitebeet.v2gEvseSetCertificateInstallationAndUpdateResponse(status, certificationResponse)
        except Warning as e:
            print("Warning: {}".format(e))
        except ConnectionError as e:
            print("ConnectionError: {}".format(e))'''
            
    def _handleMeteringReceiptStatus(self, data):
        """
        Handle the MeteringReceiptStatus notification
        """
        print("\"Metering Receipt Status\" received")
        message = self.whitebeet.v2gEvseParseMeteringReceiptStatus(data)
        print('Metering receipt status: {}'.format('verified' if message['status'] == True else 'not verified'))

    def getCharger(self):
        """
        Returns the charger object
        """
        if hasattr(self, "charger"):
            return self.charger
        else:
            return None

    def getWhitebeet(self):
        """
        Returns the whitebeet object
        """
        if hasattr(self, "whitebeet"):
            return self.whitebeet
        else:
            return None

    def setSchedule(self, schedule):
        """
        Sets the schedule. This schedule will be used when the whitebeet requests this data
        """
        if isinstance(schedule, dict) == False:
            print("Schedule needs to be of type dict")
            return False
        else:
            self.schedule = schedule
            return True

    def set_ocpp_worker(self, worker):
        self.ocpp_worker = worker

    def set_status(self, status, error_code="NoError", expiry_date=None):
        """
        Updates the internal state and notifies OCPP CSMS if connected.
        """
        if self.state != status:
            print(f"State transition: {self.state} -> {status}")
            self.state = status
            self.state_timer = time.time()
            if status == "Reserved" and expiry_date:
                from datetime import datetime
                # Convert ISO string to timestamp or store object
                # Start simplified: assuming expiry_date is iso string
                try:
                    if expiry_date.endswith('Z'): expiry_date = expiry_date[:-1] + '+00:00'
                    dt = datetime.fromisoformat(expiry_date)
                    self.reservation_expiry_time = dt.timestamp()
                    print(f"Reservation set until {dt}")
                except Exception as e:
                    print(f"Error parsing expiry: {e}")
                    self.reservation_expiry_time = None
            else:
                self.reservation_expiry_time = None

            if self.ocpp_worker:
                self.ocpp_worker.send_status_notification_threadsafe(1, status, error_code)

    def ocpp_authorize(self, id_tag):
        self.authorized_id_tag = id_tag
        print(f"EVSE Authorized by OCPP: {id_tag}")

    def loop(self):
        """
        This will handle a complete charging session of the EVSE.
        """
        self._initialize()
        if self._waitEvConnected(None):
            self._handleEvConnected()
            # Loop finished (unplugged or session ended) - return to Available logic handled in next loop iteration or explicit
            print("Session sequence finished, returning to Available...")
            self.set_status("Available")
        else:
            return False
