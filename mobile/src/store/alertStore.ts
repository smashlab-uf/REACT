import { create } from 'zustand';

type AlertStore = {
  visible: boolean;
  title: string;
  message: string;
  show: (title: string, message: string) => void;
  hide: () => void;
};

export const useAlertStore = create<AlertStore>((set) => ({
  visible: false,
  title: '',
  message: '',

  show: (title, message) => set({ visible: true, title, message }),

  hide: () => set({ visible: false }),
}));
