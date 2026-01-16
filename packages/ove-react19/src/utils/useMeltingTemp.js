import tgUseLocalStorageState from "tg-use-local-storage-state";

const useMeltingTemp = () => {
  // Provide default value to prevent JSON parse errors on corrupted localStorage
  return tgUseLocalStorageState("showMeltingTemp", { defaultValue: false });
};

export default useMeltingTemp;
