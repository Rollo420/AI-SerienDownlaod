import threading
import time

# Gemeinsame Ressource
shared_counter = 0

# Ein Lock-Objekt zur Synchronisation des Zugriffs auf shared_counter
# Dies verhindert, dass mehrere Threads gleichzeitig shared_counter ändern.
counter_lock = threading.Lock()

def increment_counter(num_increments, thread_name):
    """
    Funktion, die einen globalen Zähler inkrementiert.
    """
    global shared_counter
    print(f"[{thread_name}] Gestartet. Wird Zähler {num_increments} Mal erhöhen.")
    for _ in range(num_increments):
        # Mit 'with counter_lock:' wird sichergestellt, dass nur ein Thread
        # gleichzeitig diesen kritischen Bereich betritt.
        with counter_lock:
            current_value = shared_counter
            # Simuliere eine kleine Verzögerung, um die Race Condition zu verdeutlichen,
            # wenn das Lock nicht verwendet würde.
            time.sleep(0.001) 
            shared_counter = current_value + 1
            
    print(f"[{thread_name}] Beendet. Finaler Zählerwert aus diesem Thread: {shared_counter}")

if __name__ == "__main__":
    print("Hauptprogramm gestartet.")

    num_threads = 5
    increments_per_thread = 10000 # Jeder Thread erhöht den Zähler 10.000 Mal
    
    threads = []
    for i in range(num_threads):
        thread = threading.Thread(
            target=increment_counter,
            args=(increments_per_thread, f"Thread-{i+1}")
        )
        threads.append(thread)
        thread.start()

    # Warte, bis alle Threads beendet sind
    for thread in threads:
        thread.join()

    print("\nAlle Threads beendet.")
    print(f"Erwarteter finaler Zählerwert: {num_threads * increments_per_thread}")
    print(f"Tatsächlicher finaler Zählerwert: {shared_counter}")

    # Ohne das Lock würde der tatsächliche Zählerwert fast immer niedriger sein
    # als der erwartete Wert, da Operationen nicht atomar wären.
    print("\nBeachte: Ohne 'counter_lock' wäre der tatsächliche Wert oft falsch (Race Condition).")
    print("Das Lock stellt sicher, dass der Zugriff auf 'shared_counter' synchronisiert ist.")
    print("Hauptprogramm beendet.")
