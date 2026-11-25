from cloudrig.models import ImageTemplate, Instance, Offer
from cloudrig.vast import DEFAULT_IMAGE


class Demo:
    name = "vastai"
    default_image = DEFAULT_IMAGE

    def offers(self, query: str, disk: int = 40, count: int = 1) -> list[Offer]:
        query = query.casefold()
        rows = [
            Offer(
                "84021",
                "vastai",
                "RTX 5090",
                1,
                32,
                0.42,
                "Oregon, US",
                disk_hourly_gb=0.00014,
                disk_max=900,
                reliability=0.998,
            ),
            Offer(
                "84022",
                "vastai",
                "RTX 5090",
                1,
                32,
                0.48,
                "Frankfurt, DE",
                disk_hourly_gb=0.00012,
                disk_max=1400,
                reliability=0.999,
            ),
            Offer(
                "84023",
                "vastai",
                "RTX 5090",
                1,
                32,
                0.53,
                "Tokyo, JP",
                disk_hourly_gb=0.00013,
                disk_max=600,
                reliability=0.997,
            ),
            Offer(
                "84024",
                "vastai",
                "RTX 5090",
                2,
                32,
                0.86,
                "Quebec, CA",
                disk_hourly_gb=0.00014,
                disk_max=2000,
                reliability=0.999,
            ),
            Offer(
                "84025",
                "vastai",
                "RTX 5090",
                4,
                32,
                1.72,
                "Oregon, US",
                disk_hourly_gb=0.00014,
                disk_max=4000,
                reliability=0.999,
            ),
            Offer(
                "84026",
                "vastai",
                "RTX 4090",
                1,
                24,
                0.28,
                "Amsterdam, NL",
                disk_hourly_gb=0.00014,
                disk_max=600,
                reliability=0.996,
            ),
            Offer(
                "84027",
                "vastai",
                "A100 SXM4",
                1,
                80,
                1.15,
                "Virginia, US",
                disk_hourly_gb=0.00015,
                disk_max=1000,
                reliability=0.999,
            ),
            Offer(
                "84028",
                "vastai",
                "H100 SXM",
                1,
                80,
                2.29,
                "Frankfurt, DE",
                disk_hourly_gb=0.00014,
                disk_max=2000,
                reliability=0.999,
            ),
        ]
        return [
            row for row in rows if query in row.gpu.casefold() and row.count >= count and row.disk_max >= disk
        ]

    def instances(self) -> list[Instance]:
        return [
            Instance(
                "demo-01",
                "vastai",
                "training",
                "RTX 5090",
                1,
                "running",
                0.426,
                "Oregon, US",
                "203.0.113.10",
                22022,
            ),
            Instance(
                "demo-02",
                "vastai",
                "inference",
                "A100 SXM4",
                1,
                "running",
                1.156,
                "Virginia, US",
                "203.0.113.11",
                22023,
            ),
            Instance("demo-03", "vastai", "experiments", "RTX 4090", 1, "stopped", 0.006, "Amsterdam, NL"),
        ]

    def instance(self, identifier: str) -> Instance:
        return next(row for row in self.instances() if row.id == identifier)

    def balance(self) -> float:
        return 18.50

    def templates(self) -> list[ImageTemplate]:
        return [
            ImageTemplate("PyTorch (Vast)", "vastai/pytorch:@vastai-automatic-tag", disk=16),
            ImageTemplate("NVIDIA CUDA", "vastai/base-image:@vastai-automatic-tag", disk=32),
            ImageTemplate("vLLM", "vastai/vllm:v0.32.0-cuda-13.0", disk=24),
            ImageTemplate("SGLang", "vastai/sglang:v0.5.22-cuda-13.0", disk=24),
            ImageTemplate("Llama.cpp", "vastai/llama-cpp:b11876-mix-ec42614-cuda-13.2", disk=32),
            ImageTemplate("Ollama", "vastai/ollama:0.40.11", disk=32),
            ImageTemplate("ComfyUI", "vastai/comfy:v0.46.0-cuda-13.2-py312", disk=32),
            ImageTemplate("TensorFlow CUDA", "vastai/tensorflow:2.19.0-cuda-12.4.1", disk=16),
            ImageTemplate("PyTorch NGC", "nvcr.io/nvidia/pytorch:26.08-py3", disk=32),
        ]
