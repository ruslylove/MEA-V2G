from abc import ABC, abstractmethod

class ChargerInterface(ABC):
    """
    Abstract Interface for a Charger module.
    Implementations can be a simulation (Charger.py) or a real hardware driver (e.g., via CAN bus).
    """

    @abstractmethod
    def start(self):
        """Starts the charger operation."""
        pass

    @abstractmethod
    def stop(self):
        """Stops the charger operation, setting outputs to safe levels (usually 0)."""
        pass

    # --- Setters for EVSE (Charger) Capabilities ---
    @abstractmethod
    def setEvseMaxCurrent(self, value):
        pass

    @abstractmethod
    def setEvseMinCurrent(self, value):
        pass

    @abstractmethod
    def setEvseMaxVoltage(self, value):
        pass

    @abstractmethod
    def setEvseMinVoltage(self, value):
        pass

    @abstractmethod
    def setEvseMaxPower(self, value):
        pass

    @abstractmethod
    def setEvseDeltaVoltage(self, value):
        """Sets the slew rate / ramp-up speed for voltage per tick."""
        pass

    @abstractmethod
    def setEvseDeltaCurrent(self, value):
        """Sets the slew rate / ramp-up speed for current per tick."""
        pass

    # --- Setters for EV (Vehicle) Limits ---
    @abstractmethod
    def setEvMaxCurrent(self, value):
        pass

    @abstractmethod
    def setEvMinCurrent(self, value):
        pass

    @abstractmethod
    def setEvMaxVoltage(self, value):
        pass

    @abstractmethod
    def setEvMinVoltage(self, value):
        pass

    @abstractmethod
    def setEvMinPower(self, value):
        pass

    @abstractmethod
    def setEvMaxPower(self, value):
        pass

    # --- Dynamic Targets requested by the EV ---
    @abstractmethod
    def setEvTargetVoltage(self, voltage):
        """
        Sets the target voltage requested by the EV.
        Returns True if accepted, False if it exceeds EVSE limits.
        """
        pass

    @abstractmethod
    def setEvTargetCurrent(self, current):
        """
        Sets the target current requested by the EV.
        Returns True if accepted, False if it exceeds EVSE limits.
        """
        pass

    # --- Getters for Configuration ---
    @abstractmethod
    def getEvseMaxCurrent(self):
        pass

    @abstractmethod
    def getEvseMinCurrent(self):
        pass

    @abstractmethod
    def getEvseMaxVoltage(self):
        pass

    @abstractmethod
    def getEvseMinVoltage(self):
        pass

    @abstractmethod
    def getEvseMaxPower(self):
        pass

    @abstractmethod
    def getEvseDeltaVoltage(self):
        pass

    @abstractmethod
    def getEvseDeltaCurrent(self):
        pass

    @abstractmethod
    def getEvMaxCurrent(self):
        pass

    @abstractmethod
    def getEvMinCurrent(self):
        pass

    @abstractmethod
    def getEvMaxVoltage(self):
        pass

    @abstractmethod
    def getEvMinVoltage(self):
        pass

    @abstractmethod
    def getEvMinPower(self):
        pass

    @abstractmethod
    def getEvMaxPower(self):
        pass

    # --- Real-time Values ---
    @abstractmethod
    def getEvsePresentVoltage(self):
        """Returns the actual output voltage present at the terminals."""
        pass

    @abstractmethod
    def getEvsePresentCurrent(self):
        """Returns the actual output current flowing."""
        pass

    # --- Energy and Directional Measurands (Milestone 3) ---
    @abstractmethod
    def getEnergyActiveImportRegister(self):
        """Returns cumulative Energy.Active.Import in Wh."""
        pass

    @abstractmethod
    def getEnergyActiveExportRegister(self):
        """Returns cumulative Energy.Active.Export in Wh."""
        pass

    @abstractmethod
    def getPowerActiveImport(self):
        """Returns present Power.Active.Import (Positive if charging)."""
        pass

    @abstractmethod
    def getPowerActiveExport(self):
        """Returns present Power.Active.Export (Positive if discharging/V2G)."""
        pass

    @abstractmethod
    def getCurrentImport(self):
        """Returns present Current.Import (Positive if charging)."""
        pass

    @abstractmethod
    def getCurrentExport(self):
        """Returns present Current.Export (Positive if discharging/V2G)."""
        pass

    # --- Safety Checks ---
    @abstractmethod
    def isVoltageLimitExceeded(self, voltage):
        pass

    @abstractmethod
    def isCurrentLimitExceeded(self, current):
        pass

    @abstractmethod
    def isPowerLimitExceeded(self, power):
        pass
