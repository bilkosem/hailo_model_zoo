"""Provides utilities to preprocess images for the Detection networks."""
import cv2
import tensorflow as tf
from functools import partial

from hailo_model_zoo.core.preprocessing.roi_align_wrapper import ROIAlignWrapper

MAX_PADDING_LENGTH = 100


def _extract_box_from_image_info(image_info, max_padding_length=MAX_PADDING_LENGTH, is_normalized=True):
    horizontal_scale = tf.cast(image_info['width'], tf.float32) if is_normalized else 1
    vertical_scale = tf.cast(image_info['height'], tf.float32) if is_normalized else 1

    xmin = tf.expand_dims(
        _pad_tensor(image_info.pop('xmin') * horizontal_scale, max_padding_length), axis=1)
    xmax = tf.expand_dims(
        _pad_tensor(image_info.pop('xmax') * horizontal_scale, max_padding_length), axis=1)
    ymin = tf.expand_dims(
        _pad_tensor(image_info.pop('ymin') * vertical_scale, max_padding_length), axis=1)
    ymax = tf.expand_dims(
        _pad_tensor(image_info.pop('ymax') * vertical_scale, max_padding_length), axis=1)
    return xmin, xmax, ymin, ymax


def _cast_image_info_types(image_info, image, max_padding_length=MAX_PADDING_LENGTH):
    image_info['img_orig'] = tf.cast(image, tf.uint8)
    if 'num_boxes' in image_info:
        image_info['num_boxes'] = tf.cast(image_info['num_boxes'], tf.int32)
    if 'category_id' in image_info:
        image_info['category_id'] = _pad_tensor(image_info['category_id'], max_padding_length)
    if 'is_crowd' in image_info:
        image_info['is_crowd'] = _pad_tensor(image_info['is_crowd'], max_padding_length)
    return image_info


def _pad_tensor(x, max_tensor_padding=MAX_PADDING_LENGTH):
    paddings = [(0, 0), (0, max_tensor_padding - tf.shape(x)[0])]
    return tf.squeeze(tf.pad(tf.expand_dims(x, axis=0), paddings, "CONSTANT", constant_values=-1))


def centernet_resnet_v1_18_detection(image, image_info=None, height=None, width=None,
                                     max_pad=MAX_PADDING_LENGTH, **kwargs):
    image_info['img_orig'] = image
    image = tf.cast(image, tf.float32)
    if height and width:
        # Resize the image to the specified height and width.
        image = tf.compat.v1.image.resize_image_with_pad(image, height, width)

    if image_info and 'num_boxes' in image_info.keys():
        image_info = _cast_image_info_types(image_info, image)
        h = tf.cast(image_info['height'], tf.float32)
        w = tf.cast(image_info['width'], tf.float32)
        H = tf.cast(height, tf.float32)
        W = tf.cast(width, tf.float32)

        H_ratio = H / tf.cast(h, tf.float32)
        W_ratio = W / tf.cast(w, tf.float32)
        im_resize_ratio = tf.math.minimum(H_ratio, W_ratio)

        is_w_padded = tf.cast(tf.math.less(H_ratio, W_ratio), tf.float32)
        is_h_padded = tf.cast(tf.math.less(W_ratio, H_ratio), tf.float32)
        w_padding = tf.floor(0.5 * is_w_padded * tf.math.abs((W - w * im_resize_ratio)))
        h_padding = tf.floor(0.5 * is_h_padded * tf.math.abs((H - h * im_resize_ratio)))

        xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, max_padding_length=max_pad)

        xmin = xmin * im_resize_ratio + w_padding
        xmax = xmax * im_resize_ratio + w_padding
        ymin = ymin * im_resize_ratio + h_padding
        ymax = ymax * im_resize_ratio + h_padding

        xmin = tf.clip_by_value(xmin, 0, width - 1)
        xmax = tf.clip_by_value(xmax, 0, width - 1)
        ymin = tf.clip_by_value(ymin, 0, height - 1)
        ymax = tf.clip_by_value(ymax, 0, height - 1)

        w = xmax - xmin
        h = ymax - ymin

        # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
        image_info['height'] = height
        image_info['width'] = width
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        image_info['area'] = tf.expand_dims(
            _pad_tensor((im_resize_ratio ** 2) * image_info['area'], max_tensor_padding=max_pad), axis=1)

    return image, image_info


def yolo_v3(image, image_info=None, height=None, width=None, **kwargs):
    """This is the preprocessing used by GluonCV"""
    image = tf.cast(image, tf.float32)
    image_info['height'] = tf.shape(image)[0]
    image_info['width'] = tf.shape(image)[1]
    if height and width:
        # Resize the image to the specified height and width.
        image = tf.expand_dims(image, 0)

        # the original: image = tf.compat.v1.image.resize_bilinear(image, [height, width], align_corners=False)
        # following gluon's preprocess for yolo v3:
        is_enlarge = tf.math.logical_and(tf.math.greater(height, image_info['height']),
                                         tf.math.greater(width, image_info['width']))
        is_shrink = tf.math.logical_and(tf.math.less(height, image_info['height']),
                                        tf.math.less(width, image_info['width']))

        def resize_shrink_or_other(image, height, width):
            return tf.cond(is_shrink,
                           lambda: tf.compat.v1.image.resize_area(image, [height, width], align_corners=True),
                           lambda: tf.compat.v1.image.resize_bilinear(image, [height, width], align_corners=True))

        image = tf.cond(is_enlarge,
                        lambda: tf.compat.v1.image.resize_bicubic(image, [height, width], align_corners=True),
                        lambda: resize_shrink_or_other(image, height, width))
        image = tf.clip_by_value(image, 0.0, 255.0)
        image = tf.squeeze(image, [0])

    if image_info and 'num_boxes' in image_info.keys():
        image_info = _cast_image_info_types(image_info, image)
        xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info)

        w = xmax - xmin
        h = ymax - ymin

        # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        image_info['area'] = tf.expand_dims(_pad_tensor(image_info['area']), axis=1)

    return image, image_info


def yolo_v5(image, image_info=None, height=None, width=None,
            scope=None, color=(114, 114, 114), **kwargs):
    """
    This is the preprocessing used by ultralytics
    - Normalize the image from [0,255] to [0,1]
    """
    if height and width:
        image_shape = tf.shape(image)
        image_height = image_shape[0]
        image_width = image_shape[1]
        yolo_v5_letterbox = partial(letterbox, color=color)
        image, new_width, new_height = tf.py_func(yolo_v5_letterbox, [image, height, width],
                                                  [tf.uint8, tf.int64, tf.int64])
        image.set_shape((height, width, 3))

    if image.dtype == tf.uint8:
        image = tf.cast(image, tf.float32)

    image_info['img_orig'] = tf.cast(image, tf.uint8)
    if image_info and 'num_boxes' in image_info.keys():
        image_info = _cast_image_info_types(image_info, image)
        xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, is_normalized=True)
        w = xmax - xmin
        h = ymax - ymin
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        image_info['height'] = image_height
        image_info['width'] = image_width
        image_info['area'] = tf.expand_dims(_pad_tensor(image_info['area']), axis=1)
        image_info['letterbox_height'] = new_height
        image_info['letterbox_width'] = new_width
        image_info['horizontal_pad'] = width - new_width
        image_info['vertical_pad'] = height - new_height

    return image, image_info


def resnet_v1_18_detection(image, image_info=None, height=None, width=None,
                           max_pad=MAX_PADDING_LENGTH, **kwargs):
    image = tf.cast(image, tf.float32)
    if height and width:
        # Resize the image to the specified height and width.
        image = tf.expand_dims(image, 0)
        image = tf.compat.v1.image.resize_bilinear(image, [height, width], align_corners=False)
        image = tf.squeeze(image, [0])

    if image_info and 'num_boxes' in image_info.keys():
        image_info = _cast_image_info_types(image_info, image)
        xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, max_padding_length=max_pad)

        w = xmax - xmin
        h = ymax - ymin

        # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        image_info['area'] = tf.expand_dims(_pad_tensor(image_info['area']), axis=1)

    return image, image_info


def regnet_detection(image, image_info=None, height=None, width=None,
                     max_pad=MAX_PADDING_LENGTH, **kwargs):
    image = tf.cast(image, tf.float32)
    if height and width:
        # Resize the image to the specified height and width.
        image = tf.expand_dims(image, 0)
        image = tf.compat.v1.image.resize_bilinear(image, [height, width], align_corners=True)
        image = tf.squeeze(image, [0])

    if image_info and 'num_boxes' in image_info.keys():
        image_info = _cast_image_info_types(image_info, image)
        xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, max_padding_length=max_pad)

        w = xmax - xmin
        h = ymax - ymin

        # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        image_info['area'] = tf.expand_dims(_pad_tensor(image_info['area']), axis=1)

    return image, image_info


def mobilenet_ssd(image, image_info, height=None, width=None,
                  max_pad=MAX_PADDING_LENGTH, **kwargs):
    img_orig = image
    if image.dtype != tf.float32:
        # The following operation also changes the data range to 0-1.
        image = tf.image.convert_image_dtype(image, dtype=tf.float32)

    if height and width:
        # Resize the image to the specified height and width.
        image = tf.expand_dims(image, 0)
        image = tf.compat.v1.image.resize_bilinear(image, [height, width], align_corners=False)
        img_orig = tf.compat.v1.image.resize_bilinear(tf.expand_dims(img_orig, 0),
                                                      [height, width], align_corners=False)
        image = tf.squeeze(image, [0])

    # retrieving range 0-255 for a hostless interface
    image = image * 255

    if image_info and 'num_boxes' in image_info.keys():
        image_info['img_orig'] = tf.cast(tf.squeeze(img_orig, [0]), tf.uint8)
        image_info['num_boxes'] = tf.cast(image_info['num_boxes'], tf.int32)
        image_info['category_id'] = _pad_tensor(image_info['category_id'], max_tensor_padding=max_pad)
        image_info['is_crowd'] = _pad_tensor(image_info['is_crowd'], max_tensor_padding=max_pad)

        xmin = tf.expand_dims(_pad_tensor(image_info.pop('xmin') * tf.cast(image_info['width'], tf.float32),
                                          max_tensor_padding=max_pad), axis=1)
        xmax = tf.expand_dims(_pad_tensor(image_info.pop('xmax') * tf.cast(image_info['width'], tf.float32),
                                          max_tensor_padding=max_pad), axis=1)
        ymin = tf.expand_dims(_pad_tensor(image_info.pop('ymin') * tf.cast(image_info['height'], tf.float32),
                                          max_tensor_padding=max_pad), axis=1)
        ymax = tf.expand_dims(_pad_tensor(image_info.pop('ymax') * tf.cast(image_info['height'], tf.float32),
                                          max_tensor_padding=max_pad), axis=1)

        w = xmax - xmin
        h = ymax - ymin

        # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        image_info['area'] = tf.expand_dims(_pad_tensor(image_info['area'], max_tensor_padding=max_pad), axis=1)
    return image, image_info


def faster_rcnn_stage2(featuremap, image_info, height=None, width=None,
                       max_pad=MAX_PADDING_LENGTH, **kwargs):
    """Prepare stage2 inputs
        image - the image is the featuremap of the original image from stage 1
        image_info - is passed as is from stage 1
                     it also contains a key names - 'logits_info' whis contains data from the first stage
                     in this case - logits_info[0] is the rpn_boxes (a.k.a. proposals)
                     and logits_info[1] is the shape
    """
    # The input image at stage 2 is actually the featuremap
    # featuremap = image.set_shape((1,38,50,256))
    roi_align = ROIAlignWrapper()

    rpn_boxes = image_info['rpn_proposals']
    num_proposals = image_info.pop('num_rpn_boxes')
    image_name = image_info.pop('image_name')
    image_id = image_info.pop('image_id')
    image_info['image_name'] = tf.repeat(image_name, repeats=[num_proposals], axis=0)
    image_info['image_id'] = tf.repeat(image_id, repeats=[num_proposals], axis=0)
    featuremaps = tf.py_func(roi_align, [featuremap, rpn_boxes], [tf.float32])
    featuremaps = tf.squeeze(featuremaps)
    return featuremaps, image_info


def mobilenet_ssd_ar_preserving(image, image_info=None, height=None, width=None,
                                max_pad=MAX_PADDING_LENGTH, **kwargs):
    if height and width:
        (aspect_ratio_factor, image, image_height, image_height_float, image_width, image_width_float, new_height,
            new_width) = _ar_preserving_resize_and_crop(
            height, image, width)

    if image.dtype == tf.uint8:
        image = tf.cast(image, tf.float32)

    if image_info:
        image_info['img_orig'] = tf.cast(image, tf.uint8)
        image_info['num_boxes'] = tf.cast(image_info['num_boxes'], tf.int32)
        image_info['category_id'] = _pad_tensor(image_info['category_id'], max_tensor_padding=max_pad)
        image_info['is_crowd'] = _pad_tensor(image_info['is_crowd'], max_tensor_padding=max_pad)

        xmin = tf.expand_dims(_pad_tensor(image_info.pop('xmin') * aspect_ratio_factor * image_width_float,
                                          max_tensor_padding=max_pad), axis=1)
        xmax = tf.expand_dims(_pad_tensor(image_info.pop('xmax') * aspect_ratio_factor * image_width_float,
                                          max_tensor_padding=max_pad), axis=1)
        ymin = tf.expand_dims(_pad_tensor(image_info.pop('ymin') * aspect_ratio_factor * image_height_float,
                                          max_tensor_padding=max_pad), axis=1)
        ymax = tf.expand_dims(_pad_tensor(image_info.pop('ymax') * aspect_ratio_factor * image_height_float,
                                          max_tensor_padding=max_pad), axis=1)

        w = xmax - xmin
        h = ymax - ymin

        # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
        image_info['height'] = height
        image_info['width'] = width
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)
        if 'area' in image_info:
            image_info['area'] = tf.expand_dims(_pad_tensor(image_info['area'], max_tensor_padding=max_pad), axis=1)

    return image, image_info


def _ar_preserving_resize_and_crop(height, image, width):
    image_shape = tf.shape(image)
    image_height = image_shape[0]
    image_width = image_shape[1]
    target_height_float = tf.cast(height, dtype=tf.float32)
    target_width_float = tf.cast(width, dtype=tf.float32)
    image_height_float = tf.cast(image_height, dtype=tf.float32)
    image_width_float = tf.cast(image_width, dtype=tf.float32)
    img_expanded = tf.expand_dims(image, axis=0)
    basic_ratio = target_width_float / target_height_float
    real_ratio = image_width_float / image_height_float
    aspect_ratio_factor = tf.cond(real_ratio >= basic_ratio,
                                  lambda: target_width_float / image_width_float,
                                  lambda: target_height_float / image_height_float)
    new_height = tf.cast(aspect_ratio_factor * image_height_float, dtype=tf.int32)
    new_width = tf.cast(aspect_ratio_factor * image_width_float, dtype=tf.int32)
    image_resized = tf.compat.v1.image.resize_bilinear(img_expanded, [new_height, new_width], align_corners=False)
    padded_image = tf.pad(image_resized, [[0, 0], [0, height - new_height], [0, width - new_width], [0, 0]],
                          mode='CONSTANT', constant_values=0)
    image = tf.squeeze(padded_image, [0])
    return (aspect_ratio_factor, image, image_height, image_height_float, image_width, image_width_float, new_height,
            new_width)


def face_ssd(image, image_info=None, height=None, width=None,
             max_pad=2048, **kwargs):
    if image.dtype == tf.uint8:
        image = tf.cast(image, tf.float32)
    if height and width:
        shape = tf.shape(image)
        image_height, image_width = shape[0], shape[1]
        new_height = height
        new_width = width

        image = tf.expand_dims(image, 0)
        image = tf.compat.v1.image.resize_bilinear(image, [height, width], align_corners=False)
        image = tf.squeeze(image, [0])

    if image_info:
        image_info = _cast_image_info_types(image_info, image, max_pad)
        if 'num_boxes' in image_info.keys() and 'xmin' in image_info:
            xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, max_pad)
            w = xmax - xmin
            h = ymax - ymin
            # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
            image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)

        image_info['height'] = height
        image_info['width'] = width
        image_info['original_height'] = image_height
        image_info['original_width'] = image_width
        image_info['horizontal_pad'] = width - new_width
        image_info['vertical_pad'] = height - new_height

    return image, image_info


def retinaface(image, image_info=None, height=None, width=None,
               max_pad=2048, **kwargs):
    if image.dtype == tf.uint8:
        image = tf.cast(image, tf.float32)
    if height and width:
        (aspect_ratio_factor, image, image_height, image_height_float, image_width, image_width_float,
            new_height,
            new_width) = _ar_preserving_resize_and_crop(
            height, image, width)
    if image_info:
        image_info = _cast_image_info_types(image_info, image, max_pad)
        if 'num_boxes' in image_info.keys() and 'xmin' in image_info:
            xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, max_pad)
            w = xmax - xmin
            h = ymax - ymin
            # Arrange bbox as [xmin, ymin, w, h] to match the input needed for
            image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)

        image_info['height'] = height
        image_info['width'] = width
        image_info['original_height'] = image_height
        image_info['original_width'] = image_width
        image_info['horizontal_pad'] = width - new_width
        image_info['vertical_pad'] = height - new_height

    return image, image_info


def letterbox(img, height=608, width=1088,
              color=(127.5, 127.5, 127.5)):  # resize a rectangular image to a padded rectangular
    shape = img.shape[:2]  # shape = [height, width]
    ratio = min(float(height) / shape[0], float(width) / shape[1])
    new_shape = (round(shape[1] * ratio), round(shape[0] * ratio))  # new_shape = [width, height]
    new_width = new_shape[0]
    dw = (width - new_width) / 2  # width padding
    new_height = new_shape[1]
    dh = (height - new_height) / 2  # height padding
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    img = img[:, :, ::-1]
    img = cv2.resize(img, new_shape, interpolation=cv2.INTER_AREA)  # resized, no border
    img = cv2.copyMakeBorder(img, int(top), int(bottom), int(left), int(right), cv2.BORDER_CONSTANT,
                             value=color)  # padded rectangular
    img = img[:, :, ::-1]
    return img, new_width, new_height


def fair_mot(image, image_info=None, height=None, width=None,
             max_pad=MAX_PADDING_LENGTH, **kwargs):
    if height and width:
        image_shape = tf.shape(image)
        image_height = image_shape[0]
        image_width = image_shape[1]
        image, new_width, new_height = tf.py_func(letterbox, [image, height, width], [tf.uint8, tf.int64, tf.int64])
        image.set_shape((height, width, 3))
    if image.dtype == tf.uint8:
        image = tf.cast(image, tf.float32)
    if image_info:
        image_info = _cast_image_info_types(image_info, image, max_pad)
        xmin, xmax, ymin, ymax = _extract_box_from_image_info(image_info, max_pad, is_normalized=False)
        w = xmax - xmin
        h = ymax - ymin
        image_info['bbox'] = tf.concat([xmin, ymin, w, h], axis=1)

        image_info['height'] = height
        image_info['width'] = width
        image_info['original_height'] = image_height
        image_info['original_width'] = image_width
        image_info['horizontal_pad'] = width - new_width
        image_info['vertical_pad'] = height - new_height
        image_info['person_id'] = _pad_tensor(image_info['person_id'], max_pad)
        image_info['label'] = _pad_tensor(image_info['label'], max_pad)
        image_info['is_ignore'] = _pad_tensor(image_info['is_ignore'], max_pad)

    return image, image_info