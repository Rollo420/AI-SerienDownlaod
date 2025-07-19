import concurrent.futures
import time
import random

# Funktion, die eine Aufgabe mit einer ID simuliert
def process_task(task_id):
    """
    Simuliert die Verarbeitung einer Aufgabe.
    """
    print(f"[Task {task_id}] Verarbeitung gestartet.")
    # Simuliere variable Arbeitszeit
    work_time = random.uniform(0.5, 3.0)
    time.sleep(work_time)
    print(f"[Task {task_id}] Verarbeitung beendet in {work_time:.2f}s.")
    return f"Task {task_id} abgeschlossen."

if __name__ == "__main__":
    print("Hauptprogramm gestartet.")

    # Erstelle eine Liste von Aufgaben-IDs
    tasks = list(range(1, 11)) # 10 Aufgaben

    # --- ThreadPoolExecutor verwenden ---
    # max_workers: Die maximale Anzahl von Threads, die gleichzeitig aktiv sein dürfen.
    # Der Executor verwaltet einen Pool von Threads für dich.
    max_threads = 2 
    print(f"Erstelle einen Thread-Pool mit {max_threads} Workern.")

    # Der 'with'-Block stellt sicher, dass der Executor am Ende ordnungsgemäß heruntergefahren wird.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
        # 'executor.submit()' plant eine Funktion zur Ausführung in einem Thread aus dem Pool.
        # Es gibt ein 'Future'-Objekt zurück, das den Status der Aufgabe repräsentiert.
        # Wir speichern die Futures in einem Dictionary, um später die Ergebnisse zu verknüpfen.
        future_to_task = {executor.submit(process_task, task_id): task_id for task_id in tasks}

        print("\nAufgaben im Pool geplant. Warte auf den Abschluss...\n")

        # 'concurrent.futures.as_completed()' gibt die Futures zurück, sobald sie abgeschlossen sind.
        # Die Reihenfolge der Rückgabe ist die Reihenfolge des Abschlusses, nicht der Planung.
        for future in concurrent.futures.as_completed(future_to_task):
            task_id = future_to_task[future] # Hol die ursprüngliche Task-ID
            try:
                # 'future.result()' holt den Rückgabewert der Funktion.
                # Wenn in der Funktion eine Ausnahme aufgetreten ist, wird sie hier erneut ausgelöst.
                result = future.result()
                print(f"Ergebnis von Task {task_id}: {result}")
            except Exception as exc:
                print(f"Task {task_id} erzeugte eine Ausnahme: {exc}")

    print("\nAlle Aufgaben im Thread-Pool abgeschlossen. Hauptprogramm beendet.")
