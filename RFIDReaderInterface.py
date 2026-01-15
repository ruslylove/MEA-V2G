from abc import ABC, abstractmethod

class RFIDReaderInterface(ABC):
    """
    Abstract Interface for an RFID Reader module.
    """

    @abstractmethod
    def start(self, callback):
        """
        Starts the RFID reader.
        :param callback: Function to call when a tag is read. signature: callback(id_tag: str)
        """
        pass

    @abstractmethod
    def stop(self):
        """Stops the RFID reader."""
        pass
