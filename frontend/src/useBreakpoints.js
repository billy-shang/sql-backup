import { computed, onBeforeUnmount, onMounted, ref } from "vue";

/** 统一断点：窄屏收起侧栏并藏次要列，避免操作列被挤没。 */
export function useBreakpoints() {
  const width = ref(typeof window === "undefined" ? 1400 : window.innerWidth);

  function sync() {
    width.value = window.innerWidth;
  }

  onMounted(() => {
    sync();
    window.addEventListener("resize", sync);
  });
  onBeforeUnmount(() => window.removeEventListener("resize", sync));

  return {
    width,
    compact: computed(() => width.value < 1180),
    narrow: computed(() => width.value < 900),
  };
}
