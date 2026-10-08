type Registration = { userId: number; token: string; pending: boolean };

type Dependencies = {
  read: () => Promise<Registration | null>;
  write: (value: Registration | null) => Promise<void>;
  register: (userId: number, token: string) => Promise<unknown>;
  unregister: (userId: number, token: string) => Promise<unknown>;
};

// Serialize registration and revocation so a late registration cannot undo logout.
export function createPushRegistration(deps: Dependencies) {
  let tail: Promise<unknown> = Promise.resolve();
  function enqueue<T>(operation: () => Promise<T>): Promise<T> {
    const result = tail.then(operation);
    tail = result.catch(() => undefined);
    return result;
  }

  async function remove(record: Registration) {
    await deps.write({ ...record, pending: true });
    await deps.unregister(record.userId, record.token);
    await deps.write(null);
  }

  return {
    register: (userId: number, token: string, isCurrent: () => boolean) => enqueue(async () => {
      if (!isCurrent()) return;
      const previous = await deps.read();
      if (previous) await remove(previous);
      if (!isCurrent()) return;
      // Persist before sending: even a lost response may mean the server saved it.
      const record = { userId, token, pending: true };
      await deps.write(record);
      await deps.register(userId, token);
      if (isCurrent()) await deps.write({ ...record, pending: false });
      else await remove(record);
    }),
    unregister: () => enqueue(async () => {
      const record = await deps.read();
      if (record) await remove(record);
    }),
    retry: () => enqueue(async () => {
      const record = await deps.read();
      if (record?.pending) await remove(record);
    }),
  };
}
