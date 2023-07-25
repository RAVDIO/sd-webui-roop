from scripts.roop_swapping import swapper
import numpy as np
import base64
import io, pathlib
from dataclasses import dataclass, fields
from typing import Dict, List, Set, Tuple, Union, Optional
import dill as pickle
import gradio as gr
from insightface.app.common import Face
from PIL import Image
from scripts.roop_utils.imgutils import pil_to_cv2, convert_to_sd
from scripts.roop_logging import logger

import random
from scripts.roop_utils.models_utils import get_face_checkpoints


@dataclass
class FaceSwapUnitSettings:
    
    # ORDER of parameters is IMPORTANT. It should match the result of faceswap_unit_ui

    # The image given in reference
    source_img: Union[Image.Image, str]
    # The checkpoint file
    source_face: str
    # The batch source images
    _batch_files: Union[gr.components.File, List[Image.Image]]
    # Will blend faces if True
    blend_faces: bool
    # Enable this unit
    enable: bool
    # Use same gender filtering
    same_gender: bool
    # Sort faces by their size (from larger to smaller)
    sort_by_size : bool
    # If True, discard images with low similarity
    check_similarity: bool
    # if True will compute similarity and add it to the image info
    _compute_similarity: bool

    # Minimum similarity against the used face (reference, batch or checkpoint)
    min_sim: float
    # Minimum similarity against the reference (reference or checkpoint if checkpoint is given)
    min_ref_sim: float
    # The face index to use for swapping
    _faces_index: str
    # The face index to get image from source
    reference_face_index : int

    # Swap in the source image in img2img (before processing)
    swap_in_source: bool
    # Swap in the generated image in img2img (always on for txt2img)
    swap_in_generated: bool

    @staticmethod
    def get_unit_configuration(unit: int, components):
        fields_count = len(fields(FaceSwapUnitSettings))
        return FaceSwapUnitSettings(
            *components[unit * fields_count : unit * fields_count + fields_count]
        )

    @property
    def faces_index(self):
        """
        Convert _faces_index from str to int
        """
        faces_index = {
            int(x) for x in self._faces_index.strip(",").split(",") if x.isnumeric()
        }
        if len(faces_index) == 0:
            return {0}

        logger.debug("FACES INDEX : %s", faces_index)

        return faces_index

    @property
    def compute_similarity(self):
        return self._compute_similarity or self.check_similarity

    @property
    def batch_files(self):
        """
        Return empty array instead of None for batch files
        """
        return self._batch_files or []

    def randomize_face(self):
        self._using_random_face = True
        path = random.choice([x for x in get_face_checkpoints() if x != "None"])
        self.read_reference_face(path)
        self._rand_face_name = pathlib.Path(path).stem
        logger.info(f"Randomized face to: {self._rand_face_name}")
        return path

    @property
    def has_random_face(self):
        return hasattr(self, "_using_random_face") and self._using_random_face

    @property
    def random_face_name(self):
        return self._rand_face_name if hasattr(self, "_rand_face_name") else ""

    def read_reference_face(self, path: str):
        with open(path, "rb") as file:
            try:
                logger.info(f"loading pickle {file.name}")
                face = Face(pickle.load(file))
                self._reference_face = face
            except Exception as e:
                logger.error("Failed to load checkpoint  : %s", e)

    @property
    def reference_face(self):
        """
        Extract reference face (only once and store it for the rest of processing).
        Reference face is the checkpoint or the source image or the first image in the batch in that order.
        """
        if not hasattr(self, "_reference_face"):
            if self.source_face and self.source_face != "None":
                self.read_reference_face(self.source)
            elif self.source_img is not None:
                if isinstance(self.source_img, str):  # source_img is a base64 string
                    if (
                        "base64," in self.source_img
                    ):  # check if the base64 string has a data URL scheme
                        base64_data = self.source_img.split("base64,")[-1]
                        img_bytes = base64.b64decode(base64_data)
                    else:
                        # if no data URL scheme, just decode
                        img_bytes = base64.b64decode(self.source_img)
                    self.source_img = Image.open(io.BytesIO(img_bytes))
                source_img = pil_to_cv2(self.source_img)
                self._reference_face =  swapper.get_or_default(swapper.get_faces(source_img), self.reference_face_index, None)  
                if self._reference_face is None :
                    logger.error("Face not found in reference image")  
            else :
                self._reference_face = None

        if self._reference_face is None:
            self.randomize_face()

        return self._reference_face

    @property
    def faces(self):
        """_summary_
        Extract all faces (including reference face) to provide an array of faces
        Only processed once.
        """
        if self.has_random_face:
            return [self.reference_face]

        if self.batch_files is not None and not hasattr(self, "_faces"):
            self._faces = (
                [self.reference_face] if self.reference_face is not None else []
            )
            for file in self.batch_files:
                if isinstance(file, Image.Image):
                    img = file
                else:
                    img = Image.open(file.name)

                face = swapper.get_or_default(
                    swapper.get_faces(pil_to_cv2(img)), 0, None
                )
                if face is not None:
                    self._faces.append(face)
        return self._faces

    @property
    def blended_faces(self):
        """
        Blend the faces using the mean of all embeddings
        """
        if self.has_random_face:
            return [self.reference_face]

        if not hasattr(self, "_blended_faces"):
            self._blended_faces = swapper.blend_faces(self.faces)
            assert (
                all(
                    [
                        not np.array_equal(
                            self._blended_faces.embedding, face.embedding
                        )
                        for face in self.faces
                    ]
                )
                if len(self.faces) > 1
                else True
            ), "Blended faces cannot be the same as one of the face if len(face)>0"
            assert (
                not np.array_equal(
                    self._blended_faces.embedding, self.reference_face.embedding
                )
                if len(self.faces) > 1
                else True
            ), "Blended faces cannot be the same as reference face if len(face)>0"

        return self._blended_faces
