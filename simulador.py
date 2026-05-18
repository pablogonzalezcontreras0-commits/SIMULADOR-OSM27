import asyncio
import logging
import time
import math
import random
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext
from pymodbus.server import StartAsyncTcpServer
from fastapi import FastAPI, Body
import uvicorn

# --- 1. CONFIGURACIÓN DE LOGGING ---
logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.INFO)

# --- 2. FUENTE DE LA VERDAD (ESTADO GLOBAL) ---
estado_simulador = {
    "falla_fase1": False,
    "tension_nominal": 13200.0,
    "estado_reconectador": 0,  # 0: Cerrado, 1: Abierto, 2: Bloqueado
    "contador_aperturas": 0
}

# --- INSTANCIA DE LA API ---
app = FastAPI(title="Panel de Control OSM27 - IPSEP")

# --- RUTAS DE LA API (ENDPOINTS) ---
@app.put("/api/v1/reconectador/estado")
async def cambiar_estado_reconectador(estado: int = Body(..., embed=True)):
    """Cambia el estado mecánico del reconectador (0: Cerrado, 1: Abierto, 2: Bloqueado)"""
    if estado in [0, 1, 2]:
        estado_simulador["estado_reconectador"] = estado
        return {"mensaje": f"Estado del reconectador actualizado a {estado}"}
    return {"error": "Estado inválido. Use 0, 1 o 2."}, 400

@app.put("/api/v1/fallas/fase1")
async def alternar_falla_fase1(activa: bool = Body(..., embed=True)):
    """Inyecta o remueve una falla de cortocircuito en la Fase 1"""
    estado_simulador["falla_fase1"] = activa
    estado_texto = "INDUCIDA" if activa else "REMOVIDA"
    return {"mensaje": f"Falla en Fase 1 {estado_texto}"}

# Variable global para la memoria del flanco de subida
estado_previo_reconectador = 0 

# --- 3. MOTOR MATEMÁTICO ---
def simular_valor_rms(valor_base, variacion_ruido_pct=0.005):
    """Genera fluctuación lenta + Ruido Blanco Gaussiano (AWGN)"""
    fluctuacion_lenta = math.sin(time.time() / 1000.0) * (valor_base * 0.02)
    ruido_sensor = random.gauss(0, valor_base * variacion_ruido_pct)
    return valor_base + fluctuacion_lenta + ruido_sensor

# --- 4. TAREA ASÍNCRONA: ACTUALIZACIÓN DE DATOS ---
async def actualizar_valores(datastore):
    global estado_previo_reconectador
    
    while True:
        # A. Detección de Flanco de Subida para el Contador
        estado_actual = estado_simulador["estado_reconectador"]
        if estado_previo_reconectador == 0 and estado_actual == 1:
            estado_simulador["contador_aperturas"] += 1
            log.info(f"¡Reconectador Abierto! Contador: {estado_simulador['contador_aperturas']}")
        
        # B. Lógica de Fallas (Operador Ternario)
        v_fase1_crudo = 0.0 if estado_simulador["falla_fase1"] else simular_valor_rms(estado_simulador["tension_nominal"])
        
        # C. Escalamiento a Enteros (Resolución 1V según NOJA Power)
        valor_vfase1 = int(v_fase1_crudo)
        
        # D. Escritura en Memoria Modbus
        # Argumentos setValues: (Tipo de Tabla, Índice, [Lista de Valores])
        # Tabla 4 = Input Registers. Índice 4 = Dirección 30005.
        datastore.setValues(4, 4, [valor_vfase1])
        
        # ... [Acá escalarás el resto de tus variables analógicas y booleanas] ...
        
        # E. Actualización de la memoria de estado para el próximo ciclo
        estado_previo_reconectador = estado_actual
        
        # F. Ceder el procesador (TDM)
        await asyncio.sleep(1)

# --- 5. TAREA ASÍNCRONA: SERVIDOR MODBUS ---
async def run_server(datastore_compartido):
    # Asignamos el bloque a los Input Registers (ir)
    store = ModbusSlaveContext(di=datastore_compartido, co=datastore_compartido, hr=datastore_compartido, ir=datastore_compartido)
    context = ModbusServerContext(slaves=store, single=True)
    
    log.info("Iniciando Simulador OSM27 en 0.0.0.0:502...")
    await StartAsyncTcpServer(context=context, address=("0.0.0.0", 502))

# --- 6. PUNTO DE ENTRADA PRINCIPAL ---
if __name__ == "__main__":
    datastore_unico = ModbusSequentialDataBlock(0, [0] * 100)

    # Configuramos el servidor web para que corra en el puerto 8000
    config_api = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    servidor_api = uvicorn.Server(config_api)

    async def main():
        # ¡La triple concurrencia! Modbus + Actualización + Interfaz Web
        await asyncio.gather(
            run_server(datastore_unico),
            actualizar_valores(datastore_unico),
            servidor_api.serve()
        )

    asyncio.run(main())
