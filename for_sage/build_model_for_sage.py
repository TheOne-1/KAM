import torch
from a_load_model_and_predict import *


# define preprocess functions
def make_joints_relative_to_midhip():
    midhip_col_loc = [data_fields.index('MidHip' + axis + angle) for axis in ['_x', '_y'] for angle in ['_90', '_180']]
    key_points_to_process = ["LShoulder", "RShoulder", "RKnee", "LKnee", "RAnkle", "LAnkle"]
    for sub_name, sub_data in data_all_sub.items():
        midhip_90_and_180_data = sub_data[:, :, midhip_col_loc]
        for key_point in key_points_to_process:
            key_point_col_loc = [data_fields.index(key_point + axis + angle) for axis in ['_x', '_y'] for angle in ['_90', '_180']]
            sub_data[:, :, key_point_col_loc] = sub_data[:, :, key_point_col_loc] - midhip_90_and_180_data
        data_all_sub[sub_name] = sub_data


def normalize_array_separately(data, scalar, method, scalar_mode='by_each_column'):
    input_data = data.copy()
    original_shape = input_data.shape
    target_shape = [-1, input_data.shape[2]] if scalar_mode == 'by_each_column' else [-1, 1]
    input_data[(input_data == 0.).all(axis=2), :] = np.nan
    input_data = input_data.reshape(target_shape)
    scaled_data = getattr(scalar, method)(input_data)
    scaled_data = scaled_data.reshape(original_shape)
    scaled_data[np.isnan(scaled_data)] = 0.
    return scaled_data


def get_body_weighted_imu():
    weight_col_loc = data_fields.index('body weight')
    for sub_name, sub_data in data_all_sub.items():
        sub_weight = sub_data[0, 0, weight_col_loc]
        for segment in ['L_FOOT', 'R_FOOT', 'R_SHANK', 'R_THIGH', 'WAIST', 'CHEST', 'L_SHANK', 'L_THIGH']:
            segment_imu_col_loc = [data_fields.index(field + '_' + segment) for field in IMU_FIELDS[:6]]
            sub_data[:, :, segment_imu_col_loc[:3]] = \
                sub_data[:, :, segment_imu_col_loc[:3]] * sub_weight * SEGMENT_MASS_PERCENT[segment] / 100
        data_all_sub[sub_name] = sub_data


""" step 0: select model and load data """
# Five models are available: 8IMU_camera, 3IMU_camera, 8IMU, 3IMU, camera
model_name = '3IMU'
# Two target moments: KAM or KFM
target_moment = 'KAM'

assert model_name in ['8IMU_camera', '3IMU_camera', '8IMU', '3IMU', 'camera'], 'Incorrect model name.'
assert target_moment in ['KAM', 'KFM'], 'Incorrect target moment name.'

# one example data file is available
with h5py.File('../trained_models_and_example_data/example_data.h5', 'r') as hf:
    data_all_sub = {subject: subject_data[:] for subject, subject_data in hf.items()}
    data_fields = json.loads(hf.attrs['columns'])

import pandas as pd
df = pd.DataFrame(data_all_sub['subject_01'][0, :, :], columns=data_fields)


model_path = './trained_models_and_example_data/' + model_name + '_' + target_moment + '.pth'
model = torch.load(model_path)

""" step 1: prepare subject 01's data as input """
make_joints_relative_to_midhip()
get_body_weighted_imu()

# subject_01 or subject_02 are available;
# subject_01's data was involved in model training, while subject_02's data was not
subject_data = data_all_sub['subject_01']
model_inputs = {}
model_inputs['anthro'] = torch.from_numpy(subject_data[:, :, [data_fields.index('body weight'),
                                                              data_fields.index('body height')]])
model_inputs['step_length'] = torch.from_numpy(np.sum(~(subject_data[:, :, 0] == 0.), axis=1))

for submodel, component in zip([model.model_fx, model.model_fz, model.model_rx, model.model_rz],
                               ['force_x', 'force_z', 'r_x', 'r_z']):
    input_fields_ = submodel.input_fields
    data_to_process = copy.deepcopy(subject_data)

    other_feature_loc = [data_fields.index(field) for field in input_fields_ if 'Acc' not in field]
    data_to_process[:, :, other_feature_loc] = normalize_array_separately(
        data_to_process[:, :, other_feature_loc], model.scalars[component + '_other'], 'transform',
        scalar_mode='by_each_column')

    weighted_acc_loc = [data_fields.index(field) for field in input_fields_ if 'Acc' in field]
    if len(weighted_acc_loc) > 0:
        data_to_process[:, :, weighted_acc_loc] = normalize_array_separately(
            data_to_process[:, :, weighted_acc_loc], model.scalars[component + '_acc'], 'transform',
            scalar_mode='by_all_columns')
    submodel_input = data_to_process[:, :, [data_fields.index(field) for field in input_fields_]]
    model_inputs[component] = torch.from_numpy(submodel_input)

typical_input = (model_inputs['force_x'], model_inputs['force_z'], model_inputs['r_x'], model_inputs['r_z'],
                 model_inputs['anthro'], model_inputs['step_length'])


new_path = 'for_sage/3IMU_KAM.temp.pth'

traced_cell = torch.jit.trace(model, typical_input, strict=False)
# for submodel in [traced_cell.model_fx, traced_cell.model_fz, traced_cell.model_rx, traced_cell.model_rz]:
traced_cell.model_fx.input_fields = model.model_fx.input_fields
traced_cell.model_fz.input_fields = model.model_fz.input_fields
traced_cell.model_rx.input_fields = model.model_rx.input_fields
traced_cell.model_rz.input_fields = model.model_rz.input_fields

torch.jit.save(traced_cell, new_path)
