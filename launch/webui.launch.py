from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description() -> LaunchDescription:
    params_file = os.path.join(
        get_package_share_directory('webui'),
        'config',
        'webui.params.yaml',
    )

    return LaunchDescription([
        Node(
            package='webui',
            executable='run.py',
            name='webui',
            output='screen',
            parameters=[params_file],
        )
    ])
