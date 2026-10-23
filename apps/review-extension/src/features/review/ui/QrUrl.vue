<script setup lang="ts">
import { computed } from "vue";
import type { QrCheck } from "../../../shared/contracts/review";
const props = defineProps<{ check: QrCheck }>();
const verified = computed(() => Boolean(props.check.url && props.check.domain_valid));
const decoderLabels: Record<string, string> = { model: "模型", zxing: "ZXing", opencv: "OpenCV" };
</script>

<template>
  <div class="qr-verified-url">
    <span>识别官网网址</span
    ><a
      v-if="verified"
      :href="check.url!"
      target="_blank"
      rel="noreferrer noopener"
      >{{ check.url }}</a
    ><strong v-else>未取得已确认的官网网址</strong
    ><small v-if="verified && !check.accessible"
      >官网地址已确认，后台暂未访问成功。可点击网址人工核验。</small
    ><small
      v-if="check.message"
      class="qr-diagnostic"
      >{{ check.message }}</small
    >
  </div>
  <details
    v-if="check.raw_value"
    class="qr-technical-details"
  >
    <summary>查看技术详情</summary>
    <span v-if="check.decoder">二维码来源：{{ decoderLabels[check.decoder] ?? "未解码" }}</span
    ><span v-if="check.decode_error"
      >解码结果：{{ check.decode_error
      }}{{ check.decode_elapsed_ms != null ? `（${check.decode_elapsed_ms} ms）` : "" }}</span
    ><span>原始二维码内容</span>
    <p>
      <a
        v-if="verified"
        :href="check.url!"
        target="_blank"
        rel="noreferrer noopener"
        >{{ check.raw_value }}</a
      ><template v-else>{{ check.raw_value }}</template>
    </p>
  </details>
</template>
