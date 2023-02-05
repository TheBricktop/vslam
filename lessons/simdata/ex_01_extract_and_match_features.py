
import os

import cv2
import numpy as np

from defs import ROOT_DIR
from plotting import Col, Padding, Row, TextRenderer
from utils.colors import BGRCuteColors
from utils.cv2_but_its_typed import cv2_circle
from utils.image import take_crop_around, magnify
from vslam.datasets.simdata import SimDataStreamer
from vslam.features import OrbBasedFeatureMatcher

if __name__ == '__main__':
    dataset_path = os.path.join(ROOT_DIR, 'data/short_recording_2023-02-04--17-08-25.msgpack')

    data_streamer = SimDataStreamer.from_dataset_path(dataset_path=dataset_path)

    for obs in data_streamer.stream():

        matcher = OrbBasedFeatureMatcher.build()

        im_left = obs.left_eye_img
        im_right = obs.right_eye_img

        feature_matches = matcher.detect_and_match(im_left, im_right)

        print(f"Found {len(feature_matches)} feature matches")

        layout = Col(
            Row(Padding("desc")),
            Row(Padding('left_crop'), Padding('left')),
            Row(Padding('right_crop'), Padding('right')),
        )

        # draw the matches
        from_canvas_img = np.copy(im_left)
        to_canvas_img = np.copy(im_right)

        for match in feature_matches:
            cv2_circle(from_canvas_img, match.get_from_keypoint_px()[::-1], color=BGRCuteColors.GRASS_GREEN, radius=1,
                       thickness=1)
            cv2_circle(to_canvas_img, match.get_to_keypoint_px()[::-1], color=BGRCuteColors.GRASS_GREEN, radius=1,
                       thickness=1)

        for i, match in enumerate(feature_matches):
            from_img = np.copy(from_canvas_img)
            to_img = np.copy(to_canvas_img)

            crop_from = take_crop_around(im_left, around_point=match.get_from_keypoint_px(), crop_size=(32, 32))
            crop_to = take_crop_around(im_right, around_point=match.get_to_keypoint_px(), crop_size=(32, 32))

            cv2_circle(from_img, match.get_from_keypoint_px()[::-1], color=BGRCuteColors.ORANGE, radius=10, thickness=4)
            cv2_circle(to_img, match.get_to_keypoint_px()[::-1], color=BGRCuteColors.ORANGE, radius=10, thickness=4)

            desc = f"Match {i} out of {len(feature_matches)}. Euc dist = {match.get_pixel_distance():.2f} " \
                   f"Hamming dist = {match.get_hamming_distance():.2f}"

            img = layout.render({
                'desc': TextRenderer().render(desc),
                'left': from_img,
                'right': to_img,
                'left_crop': magnify(crop_from, factor=4.0),
                'right_crop': magnify(crop_to, factor=4.0),
            })

            # cv2.imwrite(f'imgs/feature_matching_{i:04d}.png', magnify(img, factor=0.7))
            cv2.imshow('wow', img)
            cv2.waitKey(-1)



