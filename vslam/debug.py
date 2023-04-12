import collections
import itertools
from typing import List, Dict, Optional, Iterable

import attr
import numpy as np

from plotting import Packer, Col, Row, Padding, TextRenderer
from sim.birds_eye_view_render import DisplayBirdseyeView, BirdseyeViewSpecifier
from sim.sim_types import CameraExtrinsics, RenderTriangle3d, CameraSpecs
from utils.colors import BGRCuteColors
from utils.custom_types import BGRImageArray, BGRColor
from utils.cv2_but_its_typed import cv2_circle
from utils.enum_utils import StrEnum
from utils.image import take_crop_around, magnify
from vslam.cam import CameraIntrinsics
from vslam.features import FeatureMatch
from vslam.poses import SE3_pose_to_xytheta
from vslam.transforms import px_2d_to_cam_coords_3d_homo, get_world_to_cam_coord_flip_matrix, homogenize
from vslam.types import CameraPoseSE3, TransformSE3


class GeneralDebugPanes(StrEnum):
    DESC = 'desc'

class FeatureMatchDebugPanes(StrEnum):
    LEFT = 'left'
    RIGHT = 'right'
    LEFT_CROP = 'left_crop'
    RIGHT_CROP = 'right_crop'


@attr.define
class FeatureMatchDebugger:
    ui_layout: Packer
    soft_mark_matches_on_baseline_images: bool = False

    @classmethod
    def from_defaults(cls):
        layout = Col(
            Row(Padding("desc")),
            Row(
                Padding(FeatureMatchDebugPanes.LEFT_CROP),
                Padding(FeatureMatchDebugPanes.LEFT),
                Padding(FeatureMatchDebugPanes.RIGHT),
                Padding(FeatureMatchDebugPanes.RIGHT_CROP)
            ),
        )

        return cls(ui_layout=layout)

    def get_baseline_images(
            self,
            from_img: BGRImageArray,
            to_img: BGRImageArray,
            matches: List[FeatureMatch],
    ):
        # draw the matches
        from_canvas_img = np.copy(from_img)
        to_canvas_img = np.copy(to_img)

        if self.soft_mark_matches_on_baseline_images:
            for match in matches:
                cv2_circle(from_canvas_img, match.get_from_keypoint_px()[::-1], color=BGRCuteColors.GRASS_GREEN, radius=1,
                           thickness=1)
                cv2_circle(to_canvas_img, match.get_to_keypoint_px()[::-1], color=BGRCuteColors.GRASS_GREEN, radius=1,
                           thickness=1)

        return from_canvas_img, to_canvas_img

    def get_debug_image_dict_for_match(
        self,
        from_canvas_img,
        to_canvas_img,
        match
    ) -> Dict[FeatureMatchDebugPanes, BGRImageArray]:
        from_img = np.copy(from_canvas_img)
        to_img = np.copy(to_canvas_img)

        crop_from = take_crop_around(from_canvas_img, around_point=match.get_from_keypoint_px(), crop_size=(32, 32))
        crop_to = take_crop_around(to_canvas_img, around_point=match.get_to_keypoint_px(), crop_size=(32, 32))

        cv2_circle(from_img, match.get_from_keypoint_px()[::-1], color=BGRCuteColors.ORANGE, radius=10, thickness=4)
        cv2_circle(to_img, match.get_to_keypoint_px()[::-1], color=BGRCuteColors.ORANGE, radius=10, thickness=4)

        return {
            FeatureMatchDebugPanes.LEFT: from_img,
            FeatureMatchDebugPanes.RIGHT: to_img,
            FeatureMatchDebugPanes.LEFT_CROP: magnify(crop_from, factor=4.0),
            FeatureMatchDebugPanes.RIGHT_CROP: magnify(crop_to, factor=4.0),
        }

    def render(
        self,
        from_img: BGRImageArray,
        to_img: BGRImageArray,
        matches: List[FeatureMatch],
        depths: Optional[List[float]] = None
    ) -> Iterable[BGRImageArray]:

        assert depths is None or len(depths) == len(matches), f'{len(depths)=} != {len(matches)=}'
        from_canvas_img, to_canvas_img = self.get_baseline_images(from_img, to_img, matches)

        if depths is None:
            depth_txts = [''] * len(matches)
        else:
            depth_txts = ['diverged' if depth is None else f'depth: {depth:.2f}' for depth in depths]

        for i, (match, depth_txt) in enumerate(zip(matches, depth_txts)):

            name_to_image = self.get_debug_image_dict_for_match(from_canvas_img, to_canvas_img, match)

            desc = f"Match {i} out of {len(matches)}. Euc dist = {match.get_pixel_distance():.2f} " \
                   f"Hamming dist = {match.get_hamming_distance():.2f} " + depth_txt

            name_to_image['desc'] = TextRenderer().render(desc)

            img = self.ui_layout.render(name_to_image)

            yield img


class TriangulationDebugPanes(StrEnum):
    TRIANGULATION = 'triangulation'


@attr.define
class TriangulationDebugger:
    ui_layout: Packer
    feature_match_debugger: FeatureMatchDebugger    # helps us show the raw feature match

    @classmethod
    def from_defaults(cls):
        layout = Col(
            Row(Padding(GeneralDebugPanes.DESC)),
            Row(
                Padding(FeatureMatchDebugPanes.LEFT_CROP),
                Padding(FeatureMatchDebugPanes.LEFT),
                Padding(FeatureMatchDebugPanes.RIGHT),
                Padding(FeatureMatchDebugPanes.RIGHT_CROP)
            ),
            Row(Padding(TriangulationDebugPanes.TRIANGULATION))
        )

        return cls(
            ui_layout=layout,
            feature_match_debugger=FeatureMatchDebugger.from_defaults(),
        )

    def draw_triangulation_bird_eye_view(
            self,
            baselink_pose: CameraPoseSE3,
            match: FeatureMatch,
            camera_intrinsics: CameraIntrinsics,
            camera_extrinsics: CameraExtrinsics,
            triangles: List[RenderTriangle3d],
            depth_or_none: Optional[float]
    ) -> BGRImageArray:
        """
        Things to draw:
        1) [X] triangles, but make the color bleaker
        2) [X] view cones
        NO [N] 3) baselink as a point
        4) [ ] line from left eye's focal point to the right eye's feature
        5) [ ] line from right eye's focal point to the left eye's feature
        NO [ ] 6) line from baselink to intersection of the 2 above lines
        NO [ ] 7) point at the end of line with estimated depth ???
        8) [ ] point along left eye's line that is at estimated depth away from this eye's center
        """

        display_renderer = DisplayBirdseyeView.from_view_specifier(
            view_specifier=BirdseyeViewSpecifier.from_view_center(
                view_center=(baselink_pose[0, -1], baselink_pose[1, -1]),
                world_size=(60.0, 60.0)
            )
        )

        display_renderer.draw_triangles(triangles)

        display_renderer.draw_view_cone(
            at_pose=baselink_pose @ camera_extrinsics.get_pose_of_left_cam_in_baselink(),
            camera_intrinsics=camera_intrinsics,
            whiskers_thickness_px=1
        )
        display_renderer.draw_view_cone(
            at_pose=baselink_pose @ camera_extrinsics.get_pose_of_right_cam_in_baselink(),
            camera_intrinsics=camera_intrinsics,
            whiskers_thickness_px=1
        )

        # 4) [ ] line from left eye's focal point to the left eye's feature
        left_pose = baselink_pose @ camera_extrinsics.get_pose_of_left_cam_in_baselink()
        left_img_keypoint_px = match.get_from_keypoint_px()

        right_pose = baselink_pose @ camera_extrinsics.get_pose_of_right_cam_in_baselink()
        right_img_keypoint_px = match.get_to_keypoint_px()

        def draw_point(
                pose,
                keypoint_px
        ):
            keypoint_in_img = px_2d_to_cam_coords_3d_homo(np.array([keypoint_px]), camera_intrinsics)[0]
            eff_depth = depth_or_none if depth_or_none is not None else 100.0
            keypoint_in_cam = homogenize(eff_depth * keypoint_in_img)

            # TODO: come on, use function
            world_in_flip = get_world_to_cam_coord_flip_matrix().T
            keypoint_in_cam_unflipped = world_in_flip @ keypoint_in_cam
            keypoint_in_world = pose @ keypoint_in_cam_unflipped

            start_3d = pose[:3, -1]   # write a function, comeon
            end_3d = keypoint_in_world[:3]

            start_2d = start_3d[:2]
            end_2d = end_3d[:2]

            display_renderer.draw_line_2d(
                from_pt=start_2d,
                to_pt=end_2d,
                color=BGRCuteColors.DARK_BLUE
            )

        draw_point(left_pose, left_img_keypoint_px)
        draw_point(right_pose, right_img_keypoint_px)

        return display_renderer.get_image()

    def render(
        self,
        from_img: BGRImageArray,
        to_img: BGRImageArray,
        matches: List[FeatureMatch],
        depths: List[float],
        baselink_pose: CameraPoseSE3,
        camera_intrinsics: CameraIntrinsics,
        camera_extrinsics: CameraExtrinsics,
        triangles: List[RenderTriangle3d],
    ) -> Iterable[BGRImageArray]:

        assert depths is None or len(depths) == len(matches), f'{len(depths)=} != {len(matches)=}'
        from_canvas_img, to_canvas_img = self.feature_match_debugger.get_baseline_images(from_img, to_img, matches)

        if depths is None:
            depth_txts = [''] * len(matches)
        else:
            depth_txts = ['diverged' if depth is None else f'depth: {depth:.2f}' for depth in depths]

        for i, (match, depth_txt, depth) in enumerate(zip(matches, depth_txts, depths)):

            name_to_image = self.feature_match_debugger.get_debug_image_dict_for_match(from_canvas_img, to_canvas_img, match)

            desc = f"Match {i} out of {len(matches)}. Euc dist = {match.get_pixel_distance():.2f} " \
                   f"Hamming dist = {match.get_hamming_distance():.2f} " + depth_txt

            name_to_image[GeneralDebugPanes.DESC] = TextRenderer().render(desc)
            name_to_image[TriangulationDebugPanes.TRIANGULATION] = self.draw_triangulation_bird_eye_view(
                baselink_pose=baselink_pose,
                match=match,
                camera_intrinsics=camera_intrinsics,
                camera_extrinsics=camera_extrinsics,
                triangles=triangles,
                depth_or_none=depth
            )

            img = self.ui_layout.render(name_to_image)

            yield img


class LocalisationDebugPanes(StrEnum):
    SCENE = 'scene'  # birdseye view of the scene
    POSE_DIFF = 'pose_diff'
    KEYFRAME_IMG = 'keyframe_img'
    CURRENT_IMG = 'current_img'


@attr.s(auto_attribs=True)
class LocalizationDebugger:
    ui_layout: Packer
    scene_display_renderer: DisplayBirdseyeView
    cam_specs: CameraSpecs
    keyframe_img: Optional[BGRImageArray] = None
    current_img: Optional[BGRImageArray] = None
    estimated_pose_history: collections.deque = attr.Factory(lambda: collections.deque([], maxlen=64))
    ground_truth_pose_history: collections.deque = attr.Factory(lambda: collections.deque([], maxlen=64))

    @classmethod
    def from_scene(
            cls,
            scene: List[RenderTriangle3d],
            cam_specs: CameraSpecs
    ):
        scene_display_renderer = DisplayBirdseyeView.from_view_specifier(
            view_specifier=BirdseyeViewSpecifier.from_view_center(
                view_center=(0., 0.),
                world_size=(20.0, 20.0),
                resolution=0.005
            )
        )
        scene_display_renderer.draw_triangles(scene)

        layout = Col(
            Row(
                Padding(LocalisationDebugPanes.KEYFRAME_IMG),
                Padding(LocalisationDebugPanes.CURRENT_IMG),
                Padding(LocalisationDebugPanes.SCENE),
                Padding(LocalisationDebugPanes.POSE_DIFF),
            ),
        )

        return cls(
            ui_layout=layout,
            scene_display_renderer=scene_display_renderer,
            cam_specs=cam_specs
        )

    def add_keyframe(
        self,
        keyframe_baselink_pose: TransformSE3,
        keyframe_img: BGRImageArray
    ):
        # world T baselink
        # baslink T camera
        keyframe_camera_pose = keyframe_baselink_pose @ self.cam_specs.extrinsics.get_pose_of_left_cam_in_baselink()

        self.scene_display_renderer.draw_view_cone(at_pose=keyframe_camera_pose, camera_intrinsics=self.cam_specs.intrinsics)
        self.keyframe_img = keyframe_img

    def add_pose_estimate(
        self,
        baselink_pose_groundtruth: TransformSE3,
        baselink_pose_estimate: TransformSE3,
        current_image: BGRImageArray,
    ):
        self.estimated_pose_history.append(baselink_pose_estimate)
        self.ground_truth_pose_history.append(baselink_pose_groundtruth)
        self.current_img = current_image

    def render(self):

        view_center = tuple(SE3_pose_to_xytheta(self.ground_truth_pose_history[-1])[:2])
        tracking_display_renderer = DisplayBirdseyeView.from_view_specifier(
            view_specifier=BirdseyeViewSpecifier.from_view_center(
                view_center=view_center,
                world_size=(2.5, 2.5),
                resolution=0.005
            ),
            ground_color=BGRCuteColors.OFF_WHITE
        )
        def draw_pose_history(pose_history: List[TransformSE3], color: BGRColor):
            for prev, next in itertools.pairwise(pose_history):
                tracking_display_renderer.draw_line_2d(
                    from_pt=SE3_pose_to_xytheta(prev)[:2],
                    to_pt=SE3_pose_to_xytheta(next)[:2],
                    color=color
                )

            for pose_3d in pose_history:
                pose_2d = SE3_pose_to_xytheta(pose_3d)
                tracking_display_renderer.draw_circle(pose_2d[:2], color, thickness=4)

            tracking_display_renderer.draw_3d_pose(pose_history[-1], color, )

        # draw lines
        draw_pose_history(
            pose_history = list(self.estimated_pose_history),
            color = BGRCuteColors.CRIMSON,
        )

        draw_pose_history(
            pose_history = list(self.ground_truth_pose_history),
            color = BGRCuteColors.GRASS_GREEN,
        )

        return self.ui_layout.render({
            LocalisationDebugPanes.KEYFRAME_IMG: self.keyframe_img,
            LocalisationDebugPanes.CURRENT_IMG: self.current_img,
            LocalisationDebugPanes.SCENE: magnify(self.scene_display_renderer.get_image(), 0.15),
            LocalisationDebugPanes.POSE_DIFF: tracking_display_renderer.get_image(),
        })

