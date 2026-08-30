"""gRPC microservice that embeds images and text with open_clip (CLIP).

Exposes the `embed.EmbedderService` service defined in proto/embed.proto. Both
image and text embeddings live in the same shared vector space, so external
callers may embed whichever modality they need (the stash backend uses it for
image similarity and for text->image-space hybrid search).

Generate stubs first:  make proto
Run:                    make run

Env:
    CLIP_MODEL    open_clip model name   (default ViT-B/32)
    CLIP_PRETRAIN open_clip pretrained    (default laion2b_s34b_b79k)
    GRPC_PORT     listen port             (default 50051)
"""

import io
import logging
import os
from concurrent import futures

import clip  # open_clip
import embed_pb2
import embed_pb2_grpc
import grpc
import torch
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clip-embedder")

MODEL_NAME = os.getenv("CLIP_MODEL", "ViT-B/32")
PRETRAIN = os.getenv("CLIP_PRETRAIN", "laion2b_s34b_b79k")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
GRPC_PORT = os.getenv("GRPC_PORT", "50051")


def _to_vector(tensor):
    vec = tensor / tensor.norm(dim=-1, keepdim=True)
    return vec.cpu().numpy().flatten().tolist()


class EmbedderServicer(embed_pb2_grpc.EmbedderServiceServicer):
    def __init__(self, model, tokenizer, preprocess):
        self.model = model
        self.tokenizer = tokenizer
        self.preprocess = preprocess

    def EmbedImage(self, request, context):
        try:
            img = Image.open(io.BytesIO(request.image)).convert("RGB")
            tensor = self.preprocess(img).unsqueeze(0).to(DEVICE)
            with torch.no_grad():
                vec = self.model.encode_image(tensor)
            return embed_pb2.EmbedResponse(
                vector=_to_vector(vec), dim=vec.shape[-1], model=MODEL_NAME
            )
        except Exception as exc:  # noqa: BLE001 - surface decode errors to client
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return embed_pb2.EmbedResponse()

    def EmbedText(self, request, context):
        try:
            tokens = self.tokenizer([request.text]).to(DEVICE)
            with torch.no_grad():
                vec = self.model.encode_text(tokens)
            return embed_pb2.EmbedResponse(
                vector=_to_vector(vec), dim=vec.shape[-1], model=MODEL_NAME
            )
        except Exception as exc:  # noqa: BLE001
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(exc))
            return embed_pb2.EmbedResponse()


def main():
    logger.info("loading open_clip %s/%s on %s", MODEL_NAME, PRETRAIN, DEVICE)
    model, _, preprocess = clip.create_model_and_transforms(MODEL_NAME, pretrained=PRETRAIN)
    model = model.to(DEVICE).eval()
    tokenizer = clip.get_tokenizer(MODEL_NAME)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    embed_pb2_grpc.add_EmbedderServiceServicer_to_server(
        EmbedderServicer(model, tokenizer, preprocess), server
    )

    hs = health.HealthServicer()
    hs.set("embed.EmbedderService", health_pb2.HealthCheckResponse.SERVING)
    health_pb2_grpc.add_HealthServicer_to_server(hs, server)

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    server.start()
    logger.info("clip-embedder listening on %s", GRPC_PORT)
    server.wait_for_termination()


if __name__ == "__main__":
    main()
