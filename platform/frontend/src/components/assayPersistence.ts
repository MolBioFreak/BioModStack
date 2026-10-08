export const QPCR_RAW_IMPORT_CACHE_KEY = 'bms.assay.qpcr.rawImport.v1';
export const EMPOWER_IMPORT_CACHE_KEY = 'bms.assay.hplc.empowerImport.v1';

const DB_NAME = 'bms-assay-review-cache';
const DB_VERSION = 1;
const STORE_NAME = 'snapshots';
const LOCAL_STORAGE_PREFIX = 'bms.assay.snapshot.';

export interface AssaySnapshot<TPayload> {
  schemaVersion: 1;
  savedAt: string;
  label?: string;
  payload: TPayload;
}

export function makeAssaySnapshot<TPayload>(
  payload: TPayload,
  label?: string,
  now: () => string = () => new Date().toISOString(),
): AssaySnapshot<TPayload> {
  return {
    schemaVersion: 1,
    savedAt: now(),
    label,
    payload,
  };
}

function hasIndexedDb(): boolean {
  return typeof indexedDB !== 'undefined';
}

function hasLocalStorage(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined';
}

function localStorageKey(key: string): string {
  return `${LOCAL_STORAGE_PREFIX}${key}`;
}

function openAssayCacheDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!hasIndexedDb()) {
      reject(new Error('IndexedDB is not available'));
      return;
    }

    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    request.onerror = () => reject(request.error ?? new Error('Unable to open assay cache'));
    request.onsuccess = () => resolve(request.result);
  });
}

async function withStore<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openAssayCacheDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, mode);
      const store = tx.objectStore(STORE_NAME);
      const request = action(store);
      request.onerror = () => reject(request.error ?? new Error('Assay cache request failed'));
      request.onsuccess = () => resolve(request.result);
      tx.onerror = () => reject(tx.error ?? new Error('Assay cache transaction failed'));
    });
  } finally {
    db.close();
  }
}

function saveSnapshotToLocalStorage<TPayload>(key: string, snapshot: AssaySnapshot<TPayload>): boolean {
  if (!hasLocalStorage()) return false;
  try {
    window.localStorage.setItem(localStorageKey(key), JSON.stringify(snapshot));
    return true;
  } catch {
    return false;
  }
}

function loadSnapshotFromLocalStorage<TPayload>(key: string): AssaySnapshot<TPayload> | null {
  if (!hasLocalStorage()) return null;
  try {
    const raw = window.localStorage.getItem(localStorageKey(key));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AssaySnapshot<TPayload>;
    return parsed?.schemaVersion === 1 && parsed.payload !== undefined ? parsed : null;
  } catch {
    return null;
  }
}

function clearSnapshotFromLocalStorage(key: string): void {
  if (!hasLocalStorage()) return;
  try {
    window.localStorage.removeItem(localStorageKey(key));
  } catch {
    // Ignore storage cleanup failures; cache is best-effort.
  }
}

export async function saveAssaySnapshot<TPayload>(key: string, snapshot: AssaySnapshot<TPayload>): Promise<boolean> {
  if (hasIndexedDb()) {
    try {
      await withStore('readwrite', (store) => store.put(snapshot, key));
      // Remove a smaller fallback copy if a previous browser session used localStorage.
      clearSnapshotFromLocalStorage(key);
      return true;
    } catch {
      // Fall through to localStorage for browsers with blocked/failed IndexedDB.
    }
  }
  return saveSnapshotToLocalStorage(key, snapshot);
}

export async function loadAssaySnapshot<TPayload>(key: string): Promise<AssaySnapshot<TPayload> | null> {
  if (hasIndexedDb()) {
    try {
      const snapshot = await withStore<AssaySnapshot<TPayload> | undefined>('readonly', (store) => store.get(key));
      if (snapshot?.schemaVersion === 1 && snapshot.payload !== undefined) {
        return snapshot;
      }
    } catch {
      // Fall through to localStorage for browsers with blocked/failed IndexedDB.
    }
  }
  return loadSnapshotFromLocalStorage<TPayload>(key);
}

export async function clearAssaySnapshot(key: string): Promise<void> {
  clearSnapshotFromLocalStorage(key);
  if (!hasIndexedDb()) return;
  try {
    await withStore('readwrite', (store) => store.delete(key));
  } catch {
    // Cache deletion is best-effort; do not break the analysis UI on storage failures.
  }
}
