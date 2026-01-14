import pytest
import asyncio
import time
from datetime import datetime
from ocpp.v16 import call

CP_ID = "rddQC4000001"

@pytest.mark.asyncio
async def test_evse_full_lifecycle_live(live_evse, mea_api):
    """
    Continuously perform operations: 
    Available -> Connected+Auth -> Preparing -> Charging -> Finishing -> Available.
    """
    # 1. Start with Available
    print("\n--- Phase 1: Available ---")
    await live_evse.send_status_notification(status="Available")
    await asyncio.sleep(1)

    # 2. connected+auth (Simulated by swiping RFID_SIM after plugin)
    print("\n--- Phase 2: Connected + Auth ---")
    await live_evse.send_status_notification(status="Preparing")
    
    # Authorize (Swipe)
    auth_status = await live_evse.send_authorize(id_tag="RFID_SIM")
    assert auth_status == "Accepted"
    
    # Start Transaction
    await live_evse.start_transaction(1, "RFID_SIM")
    
    # Verify we are Charging
    assert live_evse.connector_status.get(1) == "Charging"
    tx_id = live_evse.transactions.get(1)
    assert tx_id is not None
    
    # 3. Charging (Wait a bit to simulate operation)
    print("\n--- Phase 3: Charging ---")
    await asyncio.sleep(3)
    
    # 4. Stop -> Finishing
    print("\n--- Phase 4: Stop -> Finishing ---")
    await live_evse.stop_transaction(1, reason="Local")
    
    # Wait for Finishing status (captured in stop_transaction)
    assert 1 not in live_evse.transactions
    
    # 5. Unplug -> Available
    print("\n--- Phase 5: Unplug -> Available ---")
    # Verify final state is Available
    assert live_evse.connector_status.get(1) == "Available"
    
    print("Full lifecycle test PASSED.")
